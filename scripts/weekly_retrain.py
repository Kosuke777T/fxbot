#!/usr/bin/env python
"""
scripts/weekly_retrain.py

週次自動再学習ジョブ用スクリプト。

    データ取得
    -> 特徴量作成
    -> LightGBM 学習
    -> Walk-Forward 検証
    -> しきい値最適化
    -> モデル保存 & 署名 (active_model.json 更新)

実行方法（推奨: モジュール実行で import の揺れを排除）:
    python -m scripts.weekly_retrain [--config configs/config.yaml] [--dry-run]

前提:
- プロジェクトルート (fxbot/) をカレントにして実行すること
- 設定: configs/config.yaml もしくは --config で指定
- 価格CSV: data/USDJPY/ohlcv/USDJPY_M5.csv のような構造
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time


def _enrich_active_model_meta(meta: dict, model_obj=None) -> dict:
    """
    Ensure active_model.json has:
      - expected_features: list[str] (must be non-empty)
      - feature_hash: sha256("\n".join(expected_features))
    Best-effort from model_obj; fallback to meta["feature_order"]/meta["features"].
    """
    import hashlib

    exp = meta.get("expected_features") or []
    # best-effort from model
    if (not exp) and model_obj is not None:
        try:
            if hasattr(model_obj, "feature_name_"):
                exp = list(getattr(model_obj, "feature_name_"))
            elif hasattr(model_obj, "feature_names_in_"):
                exp = list(getattr(model_obj, "feature_names_in_"))
            elif hasattr(model_obj, "booster_") and hasattr(model_obj.booster_, "feature_name"):
                exp = list(model_obj.booster_.feature_name())
            elif hasattr(model_obj, "booster") and callable(getattr(model_obj, "booster", None)):
                b = model_obj.booster()
                if hasattr(b, "feature_name"):
                    exp = list(b.feature_name())
        except Exception:
            pass

    # fallback to meta itself
    if not exp:
        exp = meta.get("feature_order") or meta.get("features") or []

    # normalize
    if not isinstance(exp, list) or not exp or not all(isinstance(x, str) and x for x in exp):
        raise RuntimeError("[active_model] expected_features is empty -> cannot promote/swap model safely")

    meta["expected_features"] = list(exp)
    meta["feature_hash"] = hashlib.sha256("\n".join(meta["expected_features"]).encode("utf-8")).hexdigest()
    return meta

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import List, Optional, Tuple

import lightgbm as lgb
import numpy as np
import numpy.typing as npt
import pandas as pd
import yaml
from joblib import dump
from loguru import logger
from sklearn.metrics import roc_auc_score

# ---- 定数 (Ruff の magic number 対策も兼ねる) -----------------------------

MIN_WFO_SPLITS: int = 2
DEFAULT_CLASS_THRESHOLD: float = 0.5
MIN_FINAL_ROUNDS: int = 200  # 最終モデル num_boost_round の下限（pkl 極小化防止）

JST = UTC  # 後で必要なら Asia/Tokyo に変更してもOK


# ------------------------
# 設定読み込みまわり
# ------------------------


@dataclass
class PathsConfig:
    data_dir: Path
    models_dir: Path
    logs_dir: Path


@dataclass
class RetrainConfig:
    symbol: str
    timeframe: str
    label_horizon: int = 10  # 何バー先をラベルにするか
    min_pips: float = 1.0  # クラス分けに使う最小pips
    n_splits: int = 4  # Walk-Forward の分割数
    threshold_grid: list[float] | None = None  # None を許容

    def __post_init__(self) -> None:
        if self.threshold_grid is None:
            # DEFAULT_CLASS_THRESHOLD を中心に、少し前後を見る
            self.threshold_grid = [
                DEFAULT_CLASS_THRESHOLD - 0.05,
                DEFAULT_CLASS_THRESHOLD,
                DEFAULT_CLASS_THRESHOLD + 0.05,
                DEFAULT_CLASS_THRESHOLD + 0.10,
                DEFAULT_CLASS_THRESHOLD + 0.15,
            ]


@dataclass
class WeeklyRetrainConfig:
    paths: PathsConfig
    retrain: RetrainConfig


def load_config(config_path: Path) -> WeeklyRetrainConfig:
    """YAML 設定のロード。"""

    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml が見つかりません: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    paths_raw = raw.get("paths", {}) or {}
    runtime_raw = raw.get("runtime", {}) or {}
    ai_raw = raw.get("ai", {}) or {}
    retrain_raw = ai_raw.get("retrain", {}) or {}

    data_dir = Path(paths_raw.get("data_dir", "./data")).expanduser()
    models_dir = Path(paths_raw.get("models_dir", "./models")).expanduser()
    logs_dir = Path(paths_raw.get("logs_dir", "./logs")).expanduser()

    symbol = runtime_raw.get("symbol", "USDJPY")
    timeframe = runtime_raw.get("timeframe_exec", "M5")

    label_horizon = int(retrain_raw.get("label_horizon_bars", 10))
    min_pips = float(retrain_raw.get("min_pips", 1.0))
    n_splits = int(retrain_raw.get("wfo_n_splits", 4))

    thr_raw = retrain_raw.get("threshold_grid")
    if thr_raw is None:
        threshold_grid: list[float] | None = None
    else:
        threshold_grid = [float(x) for x in thr_raw]

    cfg = WeeklyRetrainConfig(
        paths=PathsConfig(
            data_dir=data_dir,
            models_dir=models_dir,
            logs_dir=logs_dir,
        ),
        retrain=RetrainConfig(
            symbol=symbol,
            timeframe=timeframe,
            label_horizon=label_horizon,
            min_pips=min_pips,
            n_splits=n_splits,
            threshold_grid=threshold_grid,
        ),
    )
    return cfg


# ------------------------
# データ & 特徴量
# ------------------------


def load_price_data(csv_path: Path) -> pd.DataFrame:
    """MT5 から書き出した価格CSVを読み込む。"""

    if not csv_path.exists():
        raise FileNotFoundError(f"価格CSVが見つかりません: {csv_path}")

    df = pd.read_csv(csv_path)
    if "time" not in df.columns:
        raise ValueError("CSV に 'time' 列がありません。")

    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # 列名のゆらぎに対応
    vol_col: str | None = None
    for cand in ("tick_volume", "volume", "vol"):
        if cand in df.columns:
            vol_col = cand
            break
    if vol_col is None:
        df["volume"] = 0.0
        vol_col = "volume"

    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            raise ValueError(f"CSV に '{col}' 列がありません。")

    return df[["time", "open", "high", "low", "close", vol_col]].rename(
        columns={vol_col: "volume"}
    )


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = -diff.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return atr


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    非常にシンプルな特徴量セット。
    後で core/feature_pipeline.py に差し替えてもOK。
    """

    out = pd.DataFrame(index=df.index)

    out["ret_1"] = df["close"].pct_change()
    out["ret_5"] = df["close"].pct_change(5)
    out["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    out["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    out["ema_ratio"] = out["ema_5"] / (out["ema_20"] + 1e-9)

    out["rsi_14"] = compute_rsi(df["close"], period=14)
    out["atr_14"] = compute_atr(df["high"], df["low"], df["close"], period=14)

    out["range"] = (df["high"] - df["low"]) / (df["close"].shift(1) + 1e-9)
    out["vol_chg"] = df["volume"].pct_change().fillna(0.0)

    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.dropna()
    return out


def build_labels(
    df: pd.DataFrame,
    horizon: int = 10,
    min_pips: float = 1.0,
) -> tuple[pd.Series, dict[str, int]]:
    """
    horizon 足後の方向ラベルを作る。
    - USDJPY 前提で 1pips = 0.01 として計算。
    - 上昇(min_pips超) = 1, 下降(min_pips超) = 0
      それ以外（変化が小さい）は NaN にして除外。

    Returns:
        y: ラベルSeries (1=buy, 0=sell, NaN=skip)
        skip_reasons: skip理由の内訳カウント
    """

    future = df["close"].shift(-horizon)
    delta = future - df["close"]
    pips = delta * 100.0  # USDJPY 前提
    y = pd.Series(index=df.index, dtype="float32")
    y[pips >= min_pips] = 1.0
    y[pips <= -min_pips] = 0.0

    # skip理由のカウント
    skip_reasons: dict[str, int] = {
        "horizon_insufficient": int(future.isna().sum()),  # horizonが足りない（未来データなし）
        "small_change": int(((y.isna()) & (~future.isna())).sum()),  # 変化が小さい（-min_pips < pips < min_pips）
    }

    return y, skip_reasons


def align_features_and_labels(
    feats: pd.DataFrame,
    labels: pd.Series,
) -> tuple[pd.DataFrame, pd.Series]:
    df = feats.join(labels.rename("y"), how="left")
    df = df.dropna()
    y = df.pop("y").astype(int)
    X = df
    return X, y


# ------------------------
# Walk-Forward 検証 & 学習
# ------------------------


@dataclass
class FoldResult:
    fold: int
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    logloss: float
    accuracy: float
    n_train: int
    n_val: int


@dataclass
class WFOResult:
    folds: list[FoldResult]
    mean_logloss: float
    mean_accuracy: float
    mean_auc: float


def iter_walkforward_indices(
    n_samples: int,
    n_splits: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """
    非常にシンプルな walk-forward。
    - データは既に time でソートされている前提
    - n_splits+1 個のブロックに分割し、前方累積を train、次ブロックを val にする
    """

    if n_splits < MIN_WFO_SPLITS:
        raise ValueError("n_splits は最低 2 以上を推奨します。")

    block = n_samples // (n_splits + 1)
    indices = np.arange(n_samples, dtype=int)

    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for k in range(n_splits):
        train_end = block * (k + 1)
        val_end = block * (k + 2)
        if val_end <= train_end:
            break
        train_idx = indices[:train_end]
        val_idx = indices[train_end:val_end]
        splits.append((train_idx, val_idx))
    return splits


def train_lightgbm_wfo(
    X: pd.DataFrame,
    y: pd.Series,
    cfg: RetrainConfig,
    obs_output_dir: Optional[Path] = None,
) -> Tuple[WFOResult, List[lgb.Booster], npt.NDArray[np.float64], int]:
    params: dict[str, object] = {
        "objective": "binary",
        "metric": ["binary_logloss"],
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": -1,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "force_col_wise": True,
    }

    n = len(X)
    splits = iter_walkforward_indices(n, cfg.n_splits)

    NUM_BOOST_ROUND = 500
    EARLY_STOPPING_ROUNDS = 50

    oof_pred: npt.NDArray[np.float_] = np.full(
        shape=n,
        fill_value=np.nan,
        dtype="float32",
    )
    boosters: list[lgb.Booster] = []
    fold_results: list[FoldResult] = []
    fold_obs: list[dict] = []

    for fold_idx, (tr_idx, va_idx) in enumerate(splits):
        X_tr, y_tr = X.iloc[tr_idx], y.iloc[tr_idx]
        X_va, y_va = X.iloc[va_idx], y.iloc[va_idx]

        train_data = lgb.Dataset(X_tr, label=y_tr)
        valid_data = lgb.Dataset(X_va, label=y_va)

        logger.info(
            f"[WFO] fold={fold_idx} train={len(X_tr)} val={len(X_va)} "
            f"from={tr_idx[0]} to={va_idx[-1]}"
        )

        booster = lgb.train(
            params,
            train_data,
            num_boost_round=NUM_BOOST_ROUND,
            valid_sets=[valid_data],
            valid_names=["valid"],
            callbacks=[
                lgb.early_stopping(stopping_rounds=EARLY_STOPPING_ROUNDS, verbose=False),
            ],
        )

        boosters.append(booster)

        # 【観測】fold 完了時の booster 状態（best_iteration が小さい理由の確定用）
        best_iter = getattr(booster, "best_iteration", None)
        num_trees_val = None
        current_iter_val = None
        try:
            if hasattr(booster, "num_trees"):
                num_trees_val = booster.num_trees()
        except Exception:
            pass
        try:
            if hasattr(booster, "current_iteration"):
                current_iter_val = booster.current_iteration()
        except Exception:
            pass
        best_score_val = None
        try:
            bs = getattr(booster, "best_score", None)
            if isinstance(bs, dict):
                for key in ("valid", "valid_0"):
                    if key in bs and isinstance(bs[key], dict) and "binary_logloss" in bs[key]:
                        best_score_val = bs[key]["binary_logloss"]
                        break
                if best_score_val is None:
                    for v in bs.values():
                        if isinstance(v, dict) and "binary_logloss" in v:
                            best_score_val = v["binary_logloss"]
                            break
        except Exception:
            pass

        y_proba: npt.NDArray[np.float_] = booster.predict(
            X_va,
            num_iteration=booster.best_iteration,
        )
        oof_pred[va_idx] = y_proba.astype("float32")
        p1: npt.NDArray[np.float_] = np.asarray(y_proba, dtype=np.float64).reshape(-1)

        # 【観測】検証データでの予測確率分布（1行）。pred_pos_rate と n_ge_05/n_valid の整合確認用に n_valid/n_ge_05/p1_min/p1_max/uniq4 を追加。
        p1_mean = float(np.mean(p1))
        p1_std = float(np.std(p1)) if len(p1) > 1 else 0.0
        p1_p05, p1_p50, p1_p95 = float(np.percentile(p1, 5)), float(np.percentile(p1, 50)), float(np.percentile(p1, 95))
        pred_pos_rate = float((p1 >= 0.5).mean())
        y_pos_rate = float(y_va.mean())
        n_valid = len(p1)
        n_ge_05 = int((p1 >= 0.5).sum())
        p1_min = float(np.min(p1))
        p1_max = float(np.max(p1))
        uniq4 = int(np.unique(np.round(p1, 4)).size)
        logger.info(
            "[OBS][WFO] fold_id={} n_valid={} n_ge_05={} p1_min={:.4f} p1_max={:.4f} uniq4={} "
            "p1_mean={:.4f} p1_std={:.4f} p1_p05={:.4f} p1_p50={:.4f} p1_p95={:.4f} pred_pos_rate={:.4f} y_pos_rate={:.4f}",
            fold_idx,
            n_valid,
            n_ge_05,
            p1_min,
            p1_max,
            uniq4,
            p1_mean,
            p1_std,
            p1_p05,
            p1_p50,
            p1_p95,
            pred_pos_rate,
            y_pos_rate,
        )

        # メトリクス
        eps = 1e-15
        y_clipped: npt.NDArray[np.float_] = np.clip(
            y_proba,
            eps,
            1 - eps,
        )
        logloss = float(
            -np.mean(y_va * np.log(y_clipped) + (1 - y_va) * np.log(1 - y_clipped))
        )
        if best_score_val is None:
            best_score_val = logloss

        preds_label = (y_proba >= DEFAULT_CLASS_THRESHOLD).astype(int)
        acc = float(((y_va == preds_label).sum()) / len(y_va))

        pos_rate = float(y_va.mean())
        fold_obs.append({
            "fold_id": fold_idx,
            "num_boost_round": NUM_BOOST_ROUND,
            "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
            "best_iteration": best_iter,
            "num_trees": num_trees_val,
            "current_iteration": current_iter_val,
            "best_score": best_score_val,
            "train_size": len(X_tr),
            "valid_size": len(X_va),
            "pos_rate": pos_rate,
        })
        logger.info(
            "[OBS][WFO] fold_id={} num_boost_round={} early_stopping_rounds={} "
            "best_iteration={} num_trees={} current_iteration={} best_score={:.5f} "
            "train_size={} valid_size={} pos_rate={:.4f}",
            fold_idx,
            NUM_BOOST_ROUND,
            EARLY_STOPPING_ROUNDS,
            best_iter,
            num_trees_val,
            current_iter_val,
            best_score_val,
            len(X_tr),
            len(X_va),
            pos_rate,
        )

        # 【観測】特徴量重要度（gain/split 上位10）を CSV 保存
        if obs_output_dir is not None:
            obs_output_dir.mkdir(parents=True, exist_ok=True)
            fnames = booster.feature_name()
            gain_arr = booster.feature_importance(importance_type="gain")
            split_arr = booster.feature_importance(importance_type="split")
            rows: list[dict] = []
            for imp_type, arr in (("gain", gain_arr), ("split", split_arr)):
                paired = list(zip(fnames, arr, strict=True))
                paired.sort(key=lambda x: x[1], reverse=True)
                for feat, imp in paired[:10]:
                    rows.append({"fold_id": fold_idx, "importance_type": imp_type, "feature": feat, "importance": imp})
            if rows:
                fi_path = obs_output_dir / f"fi_fold{fold_idx}.csv"
                pd.DataFrame(rows).to_csv(fi_path, index=False, encoding="utf-8")
                logger.info("[OBS][WFO] fi_fold{}.csv saved: {}", fold_idx, fi_path)

        fold_results.append(
            FoldResult(
                fold=fold_idx,
                train_start=str(tr_idx[0]),
                train_end=str(tr_idx[-1]),
                val_start=str(va_idx[0]),
                val_end=str(va_idx[-1]),
                logloss=logloss,
                accuracy=acc,
                n_train=int(len(X_tr)),
                n_val=int(len(X_va)),
            )
        )

        logger.info(f"[WFO] fold={fold_idx} logloss={logloss:.5f} acc={acc:.4f}")

    # 【観測】全 fold の best_iteration 一覧と最終採用値（中央値＋下限適用）
    best_iters_raw = [getattr(b, "best_iteration", None) for b in boosters]
    best_iters_for_median = [x if x is not None else 200 for x in best_iters_raw]
    adopted_median = int(np.median(best_iters_for_median))
    final_num_boost_round = max(adopted_median, MIN_FINAL_ROUNDS)
    logger.info(
        "[OBS][WFO] fold_best_iterations={} adopted_median={} final_num_boost_round={} MIN_FINAL_ROUNDS={}",
        best_iters_raw,
        adopted_median,
        final_num_boost_round,
        MIN_FINAL_ROUNDS,
    )

    valid_mask = ~np.isnan(oof_pred)
    mean_logloss = float("nan")
    mean_accuracy = float("nan")
    mean_auc = float("nan")
    if valid_mask.sum() > 0:
        y_valid_arr: npt.NDArray[np.int_] = y[valid_mask].to_numpy()
        p_valid: npt.NDArray[np.float_] = oof_pred[valid_mask]

        eps = 1e-15
        p_clip = np.clip(p_valid, eps, 1 - eps)
        mean_logloss = float(
            -np.mean(
                y_valid_arr * np.log(p_clip) + (1 - y_valid_arr) * np.log(1 - p_clip)
            )
        )
        preds_valid = (p_valid >= DEFAULT_CLASS_THRESHOLD).astype(int)
        mean_accuracy = float((y_valid_arr == preds_valid).sum() / len(y_valid_arr))
        try:
            mean_auc = float(roc_auc_score(y_valid_arr, p_valid))
        except ValueError:
            mean_auc = float("nan")

    wfo_result = WFOResult(
        folds=fold_results,
        mean_logloss=mean_logloss,
        mean_accuracy=mean_accuracy,
        mean_auc=mean_auc,
    )

    return wfo_result, boosters, oof_pred, final_num_boost_round


# ------------------------
# しきい値最適化
# ------------------------


def optimize_threshold(
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    grid: list[float],
    wfo_result: Optional["WFOResult"] = None,
    run_id: int | None = None,
    min_trades: int = 500,
) -> dict[str, float]:
    """
    proba >= thr でロング、proba <= (1-thr) でショートの両建て意思決定。
    - total: 勝ち=+1 負け=-1 の合計（疑似equity）
    - win_rate: 勝率
    - n_trades: 意思決定数（ロング+ショート）
    """
    # 注意: 極端なthrは「意思決定がほぼ発生しない」のでノイズになりやすい。
    # min_trades 未満は best/top3 の候補から除外（ただしCSVには残す）。

    if not grid:
        grid = [0.45, 0.50, 0.55, 0.60, 0.65]

    valid_mask = ~np.isnan(oof_pred)
    y_valid: pd.Series = y.iloc[valid_mask]
    p_valid: npt.NDArray[np.float_] = oof_pred[valid_mask]

    # wfo_result がある場合は fold ごとに val 期間で評価
    thr_rows: list[dict] = []  # fold×thr の詳細をCSVに保存する
    prefix = f"[THR][run_id={run_id}]" if run_id is not None else "[THR]"

    def eval_one(y_true: pd.Series, proba: npt.NDArray[np.float_], thr: float) -> tuple[float, float, int]:
        # long: proba >= thr, short: proba <= 1-thr
        go_long = proba >= thr
        go_short = proba <= (1.0 - thr)
        take = go_long | go_short
        n_trades = int(take.sum())
        if n_trades == 0:
            return float("nan"), 0.0, 0

        y_true_arr = y_true.values.astype(int)
        y_pred = np.where(go_long, 1, 0).astype(int)
        y_pred = y_pred[take]
        y_t = y_true_arr[take]

        wins = int((y_pred == y_t).sum())
        total = float(wins - (n_trades - wins))  # +1 for win, -1 for loss
        win_rate = float(wins / n_trades) if n_trades > 0 else 0.0
        return float(total), float(win_rate), int(n_trades)

    def log_grid_results(tag: str, results: list[tuple[float, float, float, int]]) -> None:
        # results: [(thr, total, win, n_trades), ...]
        parts = []
        for thr, total, win, n_trades in results:
            if np.isnan(total):
                parts.append(f"thr={thr:.3f} total=NaN win={win:.3f} n={n_trades}")
            else:
                parts.append(f"thr={thr:.3f} total={total:.1f} win={win:.3f} n={n_trades}")
        logger.info(f"{tag} grid_results=" + ", ".join(parts))

    # -------------------------
    # fold別評価（WFOがあれば）
    # -------------------------
    if wfo_result is not None and getattr(wfo_result, "folds", None):
        fold_results: list[tuple[float, float, float, int]] = []

        for fold_i, f in enumerate(wfo_result.folds):
            try:
                val_start = int(getattr(f, "val_start"))
                val_end = int(getattr(f, "val_end"))
            except Exception:
                continue

            if val_end <= val_start:
                continue

            y_val = y.iloc[val_start:val_end]
            p_val = oof_pred[val_start:val_end]

            results_all: list[tuple[float, float, float, int, bool]] = []  # +eligible
            for thr in grid:
                total, win, n_trades = eval_one(y_val, p_val, thr)
                eligible = (n_trades >= min_trades)
                # n_trades==0 の時は total を NaN にしてログを綺麗にする（順位付けは eligible で制御）
                if n_trades == 0:
                    total = float("nan")

                thr_rows.append(
                    {
                        "run_id": run_id,
                        "fold": fold_i,
                        "thr": float(thr),
                        "total": float(total),
                        "win_rate": float(win),
                        "n_trades": int(n_trades),
                        "eligible": bool(eligible),
                    }
                )
                results_all.append((thr, total, win, n_trades, eligible))

            # best/top3 は eligible のみで選ぶ（なければ全体から）
            eligible_only = [(thr, total, win, n) for (thr, total, win, n, ok) in results_all if ok and (not np.isnan(total))]
            any_eligible = (len(eligible_only) > 0)

            if any_eligible:
                results_sorted = sorted(eligible_only, key=lambda x: x[1], reverse=True)
            else:
                # eligible が一件も無い = このfoldは意思決定が薄すぎる/条件がおかしい
                # 仕方ないので「n_trades最大→total最大」の順で救済
                fallback = [(thr, total, win, n) for (thr, total, win, n, _) in results_all if (not np.isnan(total))]
                results_sorted = sorted(fallback, key=lambda x: (x[3], x[1]), reverse=True) if fallback else []

            if results_sorted:
                best_thr, best_total, best_win, best_n = results_sorted[0]
            else:
                # 完全に取引ゼロしかない場合
                best_thr, best_total, best_win, best_n = (grid[0], float("nan"), 0.0, 0)

            # top3候補表示（best_thrがなぜ勝ったか見るため）
            top3 = results_sorted[:3] if results_sorted else []
            top3_str = ", ".join([f"thr={t:.3f} total={tot:.1f} win={w:.3f} n={n}" for t, tot, w, n in top3])

            excluded = sum(1 for (_, _, _, n, ok) in results_all if (not ok))
            tag = f"{prefix}[fold={fold_i}]"
            if np.isnan(best_total):
                logger.info(
                    f"{tag} best_thr={best_thr:.3f} equity=NaN total=NaN win={best_win:.3f} n={best_n} "
                    f"(min_trades={min_trades} excluded={excluded}) top3: {top3_str}"
                )
            else:
                logger.info(
                    f"{tag} best_thr={best_thr:.3f} equity={best_total:.1f} total={best_total:.1f} win={best_win:.3f} n={best_n} "
                    f"(min_trades={min_trades} excluded={excluded}) top3: {top3_str}"
                )

            fold_results.append((best_thr, best_total, best_win, best_n))

    # -------------------------
    # 全体評価（OOF全体）
    # -------------------------
    results_all2: list[tuple[float, float, float, int, bool]] = []
    for thr in grid:
        total, win, n_trades = eval_one(y_valid, p_valid, thr)
        eligible = (n_trades >= min_trades)
        if n_trades == 0:
            total = float("nan")

        thr_rows.append(
            {
                "run_id": run_id,
                "fold": -1,
                "thr": float(thr),
                "total": float(total),
                "win_rate": float(win),
                "n_trades": int(n_trades),
                "eligible": bool(eligible),
            }
        )
        results_all2.append((thr, total, win, n_trades, eligible))

    eligible_only2 = [(thr, total, win, n) for (thr, total, win, n, ok) in results_all2 if ok and (not np.isnan(total))]
    if eligible_only2:
        results_sorted2 = sorted(eligible_only2, key=lambda x: x[1], reverse=True)
    else:
        fallback2 = [(thr, total, win, n) for (thr, total, win, n, _) in results_all2 if (not np.isnan(total))]
        results_sorted2 = sorted(fallback2, key=lambda x: (x[3], x[1]), reverse=True) if fallback2 else []

    if results_sorted2:
        best_thr, best_total, best_win, best_n = results_sorted2[0]
    else:
        best_thr, best_total, best_win, best_n = (grid[0], float("nan"), 0.0, 0)

    # grid_results は「全候補」をログに出す（ただし NaN 表示）
    log_grid_results(prefix, [(thr, total, win, n) for (thr, total, win, n, _) in results_all2])
    excluded2 = sum(1 for (_, _, _, _, ok) in results_all2 if (not ok))
    if np.isnan(best_total):
        logger.info(f"{prefix} best_thr={best_thr:.3f} equity=NaN n={best_n} (min_trades={min_trades} excluded={excluded2})")
    else:
        logger.info(f"{prefix} best_thr={best_thr:.3f} equity={best_total:.1f} n={best_n} (min_trades={min_trades} excluded={excluded2})")

    # CSV保存（C案）
    if run_id is not None and thr_rows:
        out_dir = Path("logs") / "retrain"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_csv = out_dir / f"thr_grid_{run_id}.csv"
        df_thr = pd.DataFrame(thr_rows)
        df_thr["min_trades"] = min_trades  # min_trades情報を追加
        df_thr.to_csv(out_csv, index=False, encoding="utf-8")
        logger.info(f"{prefix} thr_grid_csv saved: {out_csv}")

    return {
        "best_thr": float(best_thr),
        "equity": float(best_total) if (not np.isnan(best_total)) else float("nan"),
        "win_rate": float(best_win),
        "n_trades": int(best_n),
    }


def _evaluate_threshold_stability(
    cfg: WeeklyRetrainConfig,
    run_id: int,
    run_id_str: str,
    thr_info: dict[str, float],
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    wfo_result: Optional["WFOResult"],
    min_pips: float,
) -> None:
    """
    threshold安定性評価: run_id横断の集約と固定vs最適化の比較を生成。

    Step 1: run_id横断の時系列集約CSV生成
    Step 2: 固定(0.45) vs 毎回最適化の比較CSV生成
    Step 3: min_tradesを200/500/1000で振った結果を記録
    """
    base_dir = cfg.paths.logs_dir / "retrain"
    stability_dir = cfg.paths.data_dir / cfg.retrain.symbol.replace("-", "") / "lgbm" / "stability"
    stability_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: 既存のthr_grid_*.csvとreport_*.jsonを読み込んで集約
    _aggregate_threshold_runs(base_dir, stability_dir, cfg)

    # Step 2: 現在のrun_idについて、固定(0.45) vs 最適化の比較
    _compare_fixed_vs_optimized(
        cfg=cfg,
        run_id=run_id,
        run_id_str=run_id_str,
        thr_info=thr_info,
        y=y,
        oof_pred=oof_pred,
        wfo_result=wfo_result,
        min_pips=min_pips,
        stability_dir=stability_dir,
    )

    # Step 3: min_tradesを200/500/1000で振った結果をCSVに永続化
    _evaluate_multiple_min_trades(
        cfg=cfg,
        run_id=run_id,
        run_id_str=run_id_str,
        stability_dir=stability_dir,
        min_trades_values=[200, 500, 1000],
    )


def _aggregate_threshold_runs(
    base_dir: Path,
    stability_dir: Path,
    cfg: WeeklyRetrainConfig,
) -> None:
    """
    Step 1: 既存のthr_grid_*.csvとreport_*.jsonを読み込んで、
    run_id横断の時系列集約CSVを生成する。
    """
    summary_rows: list[dict] = []

    # logs/retrain/ 配下のthr_grid_*.csvを全て読み込む
    thr_grid_files = sorted(base_dir.glob("thr_grid_*.csv"))

    for thr_grid_file in thr_grid_files:
        try:
            # run_idをファイル名から抽出
            run_id_str = thr_grid_file.stem.replace("thr_grid_", "")
            try:
                run_id = int(run_id_str)
            except ValueError:
                # ファイル名が変な形式の場合はスキップ
                continue

            # thr_grid CSVを読み込み（fold=-1が全体評価）
            df_thr = pd.read_csv(thr_grid_file)
            df_all = df_thr[df_thr["fold"] == -1].copy()

            if len(df_all) == 0:
                continue

            # best_thrを取得（totalが最大のもの、eligible=Trueのみ）
            df_eligible = df_all[df_all["eligible"] == True].copy()
            if len(df_eligible) == 0:
                # eligibleが無い場合は全体から
                df_eligible = df_all[df_all["n_trades"] > 0].copy()

            if len(df_eligible) == 0:
                continue

            # totalが最大のものをbest_thrとする
            df_eligible = df_eligible.dropna(subset=["total"])
            if len(df_eligible) == 0:
                continue

            best_row = df_eligible.loc[df_eligible["total"].idxmax()]
            best_thr = float(best_row["thr"])
            best_total = float(best_row["total"])
            best_n_trades = int(best_row["n_trades"])
            best_win_rate = float(best_row["win_rate"])

            # top3を取得（total降順）
            top3_rows = df_eligible.nlargest(3, "total")
            top3_str = ", ".join([
                f"thr={r['thr']:.3f}(total={r['total']:.1f})"
                for _, r in top3_rows.iterrows()
            ])

            # report JSONから実行時刻とその他メタを取得
            report_file = base_dir / f"report_{run_id_str}.json"
            ts_source = "mtime"
            ts_iso = None
            if report_file.exists():
                try:
                    with report_file.open("r", encoding="utf-8") as f:
                        report = json.load(f)
                    ts_iso = report.get("created_at_utc")
                    if ts_iso:
                        ts_source = "report_json"
                except Exception:
                    pass

            # mtimeフォールバック
            if ts_iso is None:
                try:
                    ts_iso = datetime.fromtimestamp(thr_grid_file.stat().st_mtime, tz=UTC).isoformat()
                except Exception:
                    ts_iso = datetime.now(tz=UTC).isoformat()

            # profile/pair情報
            profile = "default"  # 既存の実装に合わせる
            pair = cfg.retrain.symbol.replace("-", "")

            # min_tradesをthr_grid CSVから取得（列があれば）
            min_trades_val = None
            if "min_trades" in df_all.columns:
                min_trades_val = int(df_all["min_trades"].iloc[0])
            else:
                # 既存CSVにはmin_trades情報がない場合、eligible=Trueの最小n_tradesを推定値とする
                df_eligible_check = df_all[df_all["eligible"] == True]
                if len(df_eligible_check) > 0:
                    min_trades_val = int(df_eligible_check["n_trades"].min())

            summary_rows.append({
                "run_id": run_id,
                "ts": ts_iso,
                "ts_source": ts_source,
                "profile": profile,
                "pair": pair,
                "min_pips": cfg.retrain.min_pips,
                "min_trades": min_trades_val,
                "best_thr": best_thr,
                "total": best_total,
                "n_trades": best_n_trades,
                "win_rate": best_win_rate,
                "top3": top3_str,
            })
        except Exception as e:
            logger.warning(f"[THR_STABILITY] failed to process {thr_grid_file}: {e}")
            continue

    if summary_rows:
        df_summary = pd.DataFrame(summary_rows)
        df_summary = df_summary.sort_values("run_id")
        summary_path = stability_dir / "thr_runs_summary.csv"
        df_summary.to_csv(summary_path, index=False, encoding="utf-8")
        logger.info(f"[THR_STABILITY] summary saved: {summary_path} (rows={len(df_summary)})")


def _evaluate_multiple_min_trades(
    cfg: WeeklyRetrainConfig,
    run_id: int,
    run_id_str: str,
    stability_dir: Path,
    min_trades_values: list[int],
) -> None:
    """
    Step 3: min_tradesを200/500/1000で振った結果を記録（実験設計のみ）。
    既存のthr_grid_{run_id}.csvを読み込んで、min_trades条件で再評価し、CSVに永続化。
    """
    base_dir = cfg.paths.logs_dir / "retrain"
    thr_grid_file = base_dir / f"thr_grid_{run_id}.csv"

    if not thr_grid_file.exists():
        logger.warning(f"[THR_STABILITY] thr_grid file not found: {thr_grid_file}")
        return

    # report JSONから実行時刻を取得
    report_file = base_dir / f"report_{run_id_str}.json"
    ts_iso = datetime.now(tz=UTC).isoformat()
    if report_file.exists():
        try:
            with report_file.open("r", encoding="utf-8") as f:
                report = json.load(f)
            ts_iso = report.get("created_at_utc", ts_iso)
        except Exception:
            # mtimeフォールバック
            try:
                ts_iso = datetime.fromtimestamp(thr_grid_file.stat().st_mtime, tz=UTC).isoformat()
            except Exception:
                pass

    eval_rows: list[dict] = []

    try:
        df_thr = pd.read_csv(thr_grid_file)
        df_all = df_thr[df_thr["fold"] == -1].copy()

        for min_trades in min_trades_values:
            try:
                # min_trades条件でeligibleを再計算
                df_all["eligible"] = df_all["n_trades"] >= min_trades

                # best_thrを取得（eligible=True、total最大）
                df_eligible = df_all[df_all["eligible"] == True].copy()
                if len(df_eligible) == 0:
                    df_eligible = df_all[df_all["n_trades"] > 0].copy()

                if len(df_eligible) == 0:
                    continue

                df_eligible = df_eligible.dropna(subset=["total"])
                if len(df_eligible) == 0:
                    continue

                best_row = df_eligible.loc[df_eligible["total"].idxmax()]
                best_thr = float(best_row["thr"])
                best_total = float(best_row["total"])
                best_n_trades = int(best_row["n_trades"])
                best_win_rate = float(best_row["win_rate"])

                # top3を取得
                top3_rows = df_eligible.nlargest(3, "total")
                top3_str = ", ".join([
                    f"thr={r['thr']:.3f}(total={r['total']:.1f})"
                    for _, r in top3_rows.iterrows()
                ])

                eval_rows.append({
                    "run_id": run_id,
                    "ts": ts_iso,
                    "min_pips": cfg.retrain.min_pips,
                    "min_trades": min_trades,
                    "best_thr": best_thr,
                    "total": best_total,
                    "n_trades": best_n_trades,
                    "win_rate": best_win_rate,
                    "top3": top3_str,
                })

                logger.info(
                    f"[THR_STABILITY][min_trades={min_trades}] "
                    f"best_thr={best_thr:.3f} total={best_total:.1f} "
                    f"n_trades={best_n_trades} win_rate={best_win_rate:.3f} "
                    f"top3: {top3_str}"
                )
            except Exception as e:
                logger.warning(f"[THR_STABILITY] failed to evaluate min_trades={min_trades}: {e}")

        # CSVに追記保存
        if eval_rows:
            eval_path = stability_dir / "thr_min_trades_eval.csv"
            df_new = pd.DataFrame(eval_rows)

            if eval_path.exists():
                try:
                    df_existing = pd.read_csv(eval_path)
                    df_out = pd.concat([df_existing, df_new], ignore_index=True)
                except Exception:
                    df_out = df_new
            else:
                df_out = df_new

            df_out = df_out.sort_values(["run_id", "min_trades"])
            df_out.to_csv(eval_path, index=False, encoding="utf-8")
            logger.info(f"[THR_STABILITY] min_trades eval saved: {eval_path} (rows={len(df_out)})")
    except Exception as e:
        logger.warning(f"[THR_STABILITY] failed to process thr_grid file: {e}")


def _compare_fixed_vs_optimized(
    cfg: WeeklyRetrainConfig,
    run_id: int,
    run_id_str: str,
    thr_info: dict[str, float],
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    wfo_result: Optional["WFOResult"],
    min_pips: float,
    stability_dir: Path,
) -> None:
    """
    Step 2: 固定(0.45) vs 最適化の比較を記録。
    """
    FIXED_THR = 0.45

    valid_mask = ~np.isnan(oof_pred)
    y_valid: pd.Series = y.iloc[valid_mask]
    p_valid: npt.NDArray[np.float_] = oof_pred[valid_mask]

    # eval_one関数を再利用（optimize_threshold内と同じロジック）
    def eval_one(y_true: pd.Series, proba: npt.NDArray[np.float_], thr: float) -> tuple[float, float, int]:
        go_long = proba >= thr
        go_short = proba <= (1.0 - thr)
        take = go_long | go_short
        n_trades = int(take.sum())
        if n_trades == 0:
            return float("nan"), 0.0, 0

        y_true_arr = y_true.values.astype(int)
        y_pred = np.where(go_long, 1, 0).astype(int)
        y_pred = y_pred[take]
        y_t = y_true_arr[take]

        wins = int((y_pred == y_t).sum())
        total = float(wins - (n_trades - wins))
        win_rate = float(wins / n_trades) if n_trades > 0 else 0.0
        return float(total), float(win_rate), int(n_trades)

    compare_rows: list[dict] = []

    # 現在のrun_idのbest_thrを取得
    best_thr_opt = float(thr_info.get("best_thr", FIXED_THR))

    # 固定thr=0.45で評価
    total_fixed, win_rate_fixed, n_trades_fixed = eval_one(y_valid, p_valid, FIXED_THR)

    # 最適化thrで評価（既存のthr_infoから取得）
    total_opt, win_rate_opt, n_trades_opt = eval_one(y_valid, p_valid, best_thr_opt)

    # report JSONから実行時刻を取得
    report_file = cfg.paths.logs_dir / "retrain" / f"report_{run_id_str}.json"
    ts_iso = datetime.now(tz=UTC).isoformat()
    if report_file.exists():
        try:
            with report_file.open("r", encoding="utf-8") as f:
                report = json.load(f)
            ts_iso = report.get("created_at_utc", ts_iso)
        except Exception:
            pass

    # min_tradesは既存のthr_infoから取得（optimize_thresholdのデフォルト=500）
    # thr_grid CSVから取得を試みる
    min_trades_used = 500  # デフォルト
    thr_grid_file = cfg.paths.logs_dir / "retrain" / f"thr_grid_{run_id}.csv"
    if thr_grid_file.exists():
        try:
            df_thr = pd.read_csv(thr_grid_file)
            # min_trades列があればそれを使う
            if "min_trades" in df_thr.columns:
                min_trades_used = int(df_thr["min_trades"].iloc[0])
        except Exception:
            pass

    compare_rows.append({
        "run_id": run_id,
        "ts": ts_iso,
        "profile": "default",
        "pair": cfg.retrain.symbol.replace("-", ""),
        "min_pips": min_pips,
        "min_trades": min_trades_used,
        "thr_fixed": FIXED_THR,
        "total_fixed": total_fixed if not np.isnan(total_fixed) else None,
        "n_trades_fixed": n_trades_fixed,
        "win_rate_fixed": win_rate_fixed,
        "thr_opt": best_thr_opt,
        "total_opt": total_opt if not np.isnan(total_opt) else None,
        "n_trades_opt": n_trades_opt,
        "win_rate_opt": win_rate_opt,
        "total_diff": (total_opt - total_fixed) if (not np.isnan(total_opt) and not np.isnan(total_fixed)) else None,
    })

    if compare_rows:
        # 既存のCSVがあれば読み込んで追記
        compare_path = stability_dir / "thr_compare_fixed_vs_opt.csv"
        if compare_path.exists():
            try:
                df_existing = pd.read_csv(compare_path)
                df_new = pd.DataFrame(compare_rows)
                df_compare = pd.concat([df_existing, df_new], ignore_index=True)
            except Exception:
                df_compare = pd.DataFrame(compare_rows)
        else:
            df_compare = pd.DataFrame(compare_rows)

        df_compare = df_compare.sort_values("run_id")
        df_compare.to_csv(compare_path, index=False, encoding="utf-8")
        logger.info(f"[THR_STABILITY] comparison saved: {compare_path} (rows={len(df_compare)})")


def save_wfo_report_and_equity(
    cfg: WeeklyRetrainConfig,
    df_prices: pd.DataFrame,
    X: pd.DataFrame,
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    wfo_result: WFOResult,
    thr_info: dict[str, float],
    dry_run: bool = False,
    run_id: int | None = None,
) -> str:
    """
    Walk-Forward の結果サマリ (report_*.json) と
    擬似的な train/test エクイティカーブ (equity_train_*.csv / equity_test_*.csv)
    を logs/retrain/ 以下に出力する。
    戻り値は run_id (ファイル名の *_run_id 部分)。
    """

    # 出力先ディレクトリを作成
    base_dir = cfg.paths.logs_dir / "retrain"
    base_dir.mkdir(parents=True, exist_ok=True)

    # 一意なIDをタイムスタンプから作る
    ts = datetime.now(tz=UTC)
    if run_id is None:
        run_id = int(ts.timestamp())
    run_id_str = str(run_id)

    # ---- エクイティ用の下ごしらえ ---------------------------------
    # X と y は df_prices の一部なので、その time を合わせる
    # （align_features_and_labels のあとの X.index は df_prices.index のサブセット）
    df_all = pd.DataFrame(
        {
            "time": df_prices.loc[X.index, "time"].to_numpy(),
            "y": y.to_numpy(),
            "proba": oof_pred,
        }
    ).reset_index(drop=True)

    # NaN は「トレードしない」とみなす
    best_thr = float(thr_info.get("best_thr", DEFAULT_CLASS_THRESHOLD))

    def make_equity_curve(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, float]]:
        """
        proba >= best_thr のときだけ「+1 / -1」のトレードとして
        疑似エクイティを作る。
        equity: 累積損益（初期 0）
        signal: +1 (勝ちトレード), -1 (負けトレード), 0 (ノートレード)
        """
        equity_list: list[float] = []
        signal_list: list[int] = []
        pnl_list: list[float] = []

        equity = 0.0
        for _, row in df.iterrows():
            p = float(row["proba"])
            sig = 0
            if not np.isnan(p) and p >= best_thr:
                pnl = 1.0 if int(row["y"]) == 1 else -1.0
                equity += pnl
                pnl_list.append(pnl)
                sig = 1 if pnl > 0 else -1

            equity_list.append(equity)
            signal_list.append(sig)

        out = pd.DataFrame(
            {
                "time": df["time"].to_numpy(),
                "equity": equity_list,
                "signal": signal_list,
            }
        )

        n_trades = len(pnl_list)
        total = float(sum(pnl_list)) if pnl_list else 0.0
        wins = float(sum(p > 0 for p in pnl_list)) if pnl_list else 0.0
        gross_profit = float(sum(p for p in pnl_list if p > 0.0))
        gross_loss = float(-sum(p for p in pnl_list if p < 0.0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        winrate = wins / n_trades if n_trades > 0 else 0.0

        stats = {
            "n_trades": float(n_trades),
            "total_pnl": total,
            "win_rate": winrate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": pf,
        }
        return out, stats

    # ざっくり 70% を train、残り 30% を test として分割
    n_all = len(df_all)
    split = int(n_all * 0.7)
    df_train = df_all.iloc[:split].copy()
    df_test = df_all.iloc[split:].copy()

    eq_train_df, stats_train = make_equity_curve(df_train)
    eq_test_df, stats_test = make_equity_curve(df_test)

    # ---- CSV 出力 ----------------------------------------------------
    equity_train_path = base_dir / f"equity_train_{run_id_str}.csv"
    equity_test_path = base_dir / f"equity_test_{run_id_str}.csv"

    eq_train_df.to_csv(equity_train_path, index=False)
    eq_test_df.to_csv(equity_test_path, index=False)

    logger.info(f"[WFO] equity_train saved: {equity_train_path}")
    logger.info(f"[WFO] equity_test  saved: {equity_test_path}")

    # ---- JSON レポート出力 -------------------------------------------
    report = {
        "run_id": run_id_str,
        "created_at_utc": ts.isoformat(),
        "symbol": cfg.retrain.symbol,
        "timeframe": cfg.retrain.timeframe,
        "label_horizon_bars": cfg.retrain.label_horizon,
        "min_pips": cfg.retrain.min_pips,
        "n_samples": int(len(X)),
        "wfo": {
            "mean_logloss": wfo_result.mean_logloss,
            "mean_accuracy": wfo_result.mean_accuracy,
            "folds": [asdict(f) for f in wfo_result.folds],
        },
        "threshold": thr_info,
        "equity_train_stats": stats_train,
        "equity_test_stats": stats_test,
        "data_range": {
            "from": df_prices["time"].min().isoformat(),
            "to": df_prices["time"].max().isoformat(),
        },
    }

    report_path = base_dir / f"report_{run_id_str}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"[WFO] report saved: {report_path}")

    # ---- Threshold stability評価（report保存直後） ----
    if not dry_run:
        try:
            _evaluate_threshold_stability(
                cfg=cfg,
                run_id=run_id,
                run_id_str=run_id_str,
                thr_info=thr_info,
                y=y,
                oof_pred=oof_pred,
                wfo_result=wfo_result,
                min_pips=cfg.retrain.min_pips,
            )
        except Exception as e:
            # retrain本体を落とさない。ログで追えるようにする。
            logger.exception(f"[THR_STABILITY] evaluation failed: {e}")

    # ---- WFO stability評価（report保存直後） ----
    if dry_run:
        logger.info("[WFO] stability evaluation skipped (dry-run mode)")
    else:
        try:
            from app.services.wfo_stability_service import evaluate_wfo_stability

            # max_drawdown をエクイティカーブから計算
            def calc_max_drawdown(equity_series: pd.Series) -> float:
                """エクイティカーブから最大ドローダウンを計算"""
                if len(equity_series) == 0:
                    return 0.0
                cummax = equity_series.cummax()
                drawdown = (equity_series - cummax) / cummax.clip(lower=1e-10)
                return float(abs(drawdown.min()))

            max_dd_train = calc_max_drawdown(eq_train_df["equity"])
            max_dd_test = calc_max_drawdown(eq_test_df["equity"])

            # equity_train_stats / equity_test_stats から metrics_wfo 形式を構築
            # evaluate_wfo_stability が期待する形式:
            # {
            #   "train": {"trades": int, "total_return": float, "max_drawdown": float, "profit_factor": float, ...},
            #   "test": {"trades": int, "total_return": float, "max_drawdown": float, "profit_factor": float, ...}
            # }
            metrics_wfo = {
                "train": {
                    "trades": int(stats_train.get("n_trades", 0)),
                    "total_return": float(stats_train.get("total_pnl", 0.0)),
                    "max_drawdown": max_dd_train,
                    "profit_factor": float(stats_train.get("profit_factor", 0.0)),
                },
                "test": {
                    "trades": int(stats_test.get("n_trades", 0)),
                    "total_return": float(stats_test.get("total_pnl", 0.0)),
                    "max_drawdown": max_dd_test,
                    "profit_factor": float(stats_test.get("profit_factor", 0.0)),
                },
            }

            # metrics_path は存在しない場合もあるため None を許容
            metrics_path = None

            # evaluate_wfo_stability を呼び出し（内部で save_stability_result が呼ばれる）
            stability_result = evaluate_wfo_stability(
                metrics_wfo,
                report_path=str(report_path),
                metrics_path=metrics_path,
                run_id=run_id_str,
            )
            logger.info(
                f"[WFO] stability evaluated: stable={stability_result.get('stable')} "
                f"score={stability_result.get('score')} run_id={run_id}"
            )
        except ImportError as e:
            # app モジュールが無い環境（dry-run等）では INFO 1行でスキップ
            logger.info(f"[WFO] stability evaluation skipped: {e}")
        except Exception as e:
            # retrain本体を落とさない。ログで追えるようにする。
            logger.exception(f"[WFO] stability evaluation failed: {e}")

    return run_id


# ------------------------
# モデル保存 & 署名
# ------------------------
#
# 【観測用メモ】weekly_retrain.py が書き出す active_model.json の仕様
#
# 1) active_model.json に書き出しているキー一覧（save_model_and_meta 内の active 辞書）:
#    - model_name: str  （固定 "LightGBM_clf"）
#    - file: str        （例: "LightGBM_clf_20260122_235026.pkl"）
#    - meta_file: str  （例: "LightGBM_clf_20260122_235026.pkl.meta.json"）
#    - version: float  （timestamp）
#    - best_threshold: float | None  （threshold_info.get("best_thr")）
#    - feature_order: list[str]  （feature_cols のコピー）
#    - features: list[str]       （feature_cols のコピー）
#    注意: expected_features / feature_hash / model_path は書き出していない。
#    _enrich_active_model_meta() は定義されているが本スクリプト内では呼ばれていない（重要観測ポイント）。
#
# 2) モデル保存形式:
#    - joblib.dump(booster, model_path) で保存。
#    - booster は lgb.Booster（lgb.train() の戻り値）。sklearn 互換の LGBMClassifier ではない。
#    - したがって classes_ 属性は存在しない（Booster 単体）。
#
# 3) feature_order / features の意味と生成元:
#    - ともに save_model_and_meta(feature_cols=...) の feature_cols。
#    - 呼び出し元では list(X.columns)。X は align_features_and_labels(feats, labels) の戻り値の列（特徴量のみ）。
#    - つまり学習時に使った特徴量名のリスト。


# sklearn 互換ラッパーは core.ai.loader で定義（pickle 復元時にモジュールから解決するため）
from core.ai.loader import _LGBBoosterSklearnWrapper  # noqa: F401  # 保存物の型


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def save_model_and_meta(  # noqa: PLR0913  (引数多めでもここはOKとする)
    booster: lgb.Booster,
    cfg: WeeklyRetrainConfig,
    wfo_result: WFOResult,
    threshold_info: dict[str, float],
    feature_cols: list[str],
    data_info: dict[str, str],
) -> Path:
    cfg.paths.models_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(tz=UTC)
    ts_str = ts.strftime("%Y%m%d_%H%M%S")
    version = ts.timestamp()

    model_name = f"LightGBM_clf_{ts_str}.pkl"
    model_path = cfg.paths.models_dir / model_name

    # sklearn 互換ラッパー（classes_ を持つ）として保存し、AISvc が確定ルートに入れるようにする
    wrapper = _LGBBoosterSklearnWrapper(booster, list(feature_cols))

    # 【観測】保存直前の booster 状態（pkl 容量異常小の原因確定用）
    num_trees_val = None
    current_iter_val = None
    best_iter_val = getattr(booster, "best_iteration", None)
    model_str_len = None
    try:
        if hasattr(booster, "num_trees"):
            num_trees_val = booster.num_trees()
    except Exception as e:
        num_trees_val = f"err:{e!r}"
    try:
        if hasattr(booster, "current_iteration"):
            current_iter_val = booster.current_iteration()
    except Exception as e:
        current_iter_val = f"err:{e!r}"
    try:
        if hasattr(booster, "model_to_string"):
            model_str_len = len(booster.model_to_string())
    except Exception as e:
        model_str_len = f"err:{e!r}"
    logger.info(
        "[OBS] pre_save booster: booster_type={} wrapper_type={} num_trees={} current_iteration={} best_iteration={} model_to_string_len={}",
        type(booster).__name__,
        type(wrapper).__name__,
        num_trees_val,
        current_iter_val,
        best_iter_val,
        model_str_len,
    )

    dump(wrapper, model_path)

    pkl_size_bytes = model_path.stat().st_size
    logger.info("[OBS] post_save pkl file_size_bytes={} path={}", pkl_size_bytes, model_path)

    sha = sha256_file(model_path)

    meta = {
        "model_name": "LightGBM_clf",
        "file": model_name,
        "created_at_utc": ts.isoformat(),
        "version": version,
        "symbol": cfg.retrain.symbol,
        "timeframe": cfg.retrain.timeframe,
        "label_horizon_bars": cfg.retrain.label_horizon,
        "min_pips": cfg.retrain.min_pips,
        "features": list(feature_cols),
        "metrics": {
            "logloss": float(wfo_result.mean_logloss),
            "auc": float(wfo_result.mean_auc),
        },
        "wfo": {
            "mean_logloss": wfo_result.mean_logloss,
            "mean_accuracy": wfo_result.mean_accuracy,
            "folds": [asdict(f) for f in wfo_result.folds],
        },
        "threshold": threshold_info,
        "data": data_info,
        "sha256": sha,
    }

    meta_path = cfg.paths.models_dir / f"{model_name}.meta.json"
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"[SAVE] model={model_path} sha256={sha}")
    logger.info(f"[SAVE] meta={meta_path}")

    active = {
        "model_name": "LightGBM_clf",
        "file": model_name,
        "meta_file": meta_path.name,
        "version": version,
        "best_threshold": threshold_info.get("best_thr"),
        "feature_order": list(feature_cols),
        "features": list(feature_cols),
    }
    active = _enrich_active_model_meta(active, wrapper)
    active_path = cfg.paths.models_dir / "active_model.json"
    with active_path.open("w", encoding="utf-8") as f:
        json.dump(active, f, ensure_ascii=False, indent=2)

    logger.info(f"[SAVE] active_model={active_path}")
    # 観測: 書き出し直後の active_model.json とモデル実体をログで明示（ミチビキ側との突き合わせ用）
    logger.info(
        "[OBS] active_model.json written: path={} keys={} model_type={} classes_={}",
        str(active_path.resolve()),
        list(active.keys()),
        type(wrapper).__name__,
        "あり（classes_={}）".format(list(wrapper.classes_)),
    )
    # ------------------------
    # 【観測まとめ】weekly_retrain 書き出し vs AISvc/GUI 参照
    # ------------------------
    # 保存物: _LGBBoosterSklearnWrapper（classes_=[0,1], feature_name_, predict_proba）を pkl で保存。
    # active_model.json: _enrich_active_model_meta 適用により expected_features / feature_hash を含む。
    return model_path


# ------------------------
# メイン処理
# ------------------------


def run_weekly_retrain(cfg: WeeklyRetrainConfig, dry_run: bool = False) -> None:
    paths = cfg.paths
    rt = cfg.retrain

    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    # 同日複数回実行でログが混ざるのを防ぐため、時刻まで含める
    dt_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = paths.logs_dir / f"weekly_retrain_{dt_str}.log"
    logger.add(log_file, encoding="utf-8")

    logger.info(
        f"[CFG] symbol={rt.symbol} tf={rt.timeframe} label_horizon={rt.label_horizon} min_pips={rt.min_pips}"
    )

    # config の symbol が "USDJPY-" でも、
    # 実データは data/USDJPY/ohlcv/USDJPY_M5.csv を読む
    symbol_dir = rt.symbol.replace("-", "")
    symbol_file = rt.symbol.replace("-", "")

    csv_path = (
        paths.data_dir / symbol_dir / "ohlcv" / f"{symbol_file}_{rt.timeframe}.csv"
    )

    logger.info(f"[STEP] load_price_data csv={csv_path}")
    df_prices = load_price_data(csv_path)
    logger.info(
        f"[STEP] loaded rows={len(df_prices)} "
        f"from={df_prices['time'].min()} to={df_prices['time'].max()}"
    )

    logger.info("[STEP] build_features")
    feats = build_features(df_prices)
    logger.info(f"[STEP] features shape={feats.shape}")

    logger.info("[STEP] build_labels")
    labels, skip_reasons = build_labels(
        df_prices,
        horizon=rt.label_horizon,
        min_pips=rt.min_pips,
    )

    # ===== ラベル比率の観測（DATA BALANCE）[RAW] =====
    # build_labels() 直後：skip含む全データ
    total_raw = len(labels)
    buy_raw = int((labels == 1).sum())
    sell_raw = int((labels == 0).sum())
    skip_raw = int(labels.isna().sum())

    buy_raw_pct = (buy_raw / total_raw * 100.0) if total_raw > 0 else 0.0
    sell_raw_pct = (sell_raw / total_raw * 100.0) if total_raw > 0 else 0.0
    skip_raw_pct = (skip_raw / total_raw * 100.0) if total_raw > 0 else 0.0

    skip_horizon = skip_reasons.get("horizon_insufficient", 0)
    skip_small = skip_reasons.get("small_change", 0)
    skip_horizon_pct = (skip_horizon / total_raw * 100.0) if total_raw > 0 else 0.0
    skip_small_pct = (skip_small / total_raw * 100.0) if total_raw > 0 else 0.0

    logger.info(
        "[DATA BALANCE][RAW]\n"
        f"  total_samples: {total_raw}\n"
        f"  buy:  {buy_raw:6d} ({buy_raw_pct:5.1f}%)\n"
        f"  sell: {sell_raw:6d} ({sell_raw_pct:5.1f}%)\n"
        f"  skip: {skip_raw:6d} ({skip_raw_pct:5.1f}%)\n"
        f"    skip_reason_horizon_insufficient: {skip_horizon:6d} ({skip_horizon_pct:5.1f}%)\n"
        f"    skip_reason_small_change: {skip_small:6d} ({skip_small_pct:5.1f}%)\n"
        f"  label_definition:\n"
        f"    buy  = 1 (pips >= min_pips, 上昇)\n"
        f"    sell = 0 (pips <= -min_pips, 下降)\n"
        f"    skip = NaN (horizon不足 or 変化が小さい)\n"
        f"  source:\n"
        f"    file: scripts/weekly_retrain.py\n"
        f"    around: L850 (build_labels直後)\n"
        f"    label_gen: L262-L280 (build_labels関数)"
    )

    logger.info("[STEP] align_features_and_labels")
    X, y = align_features_and_labels(feats, labels)

    # ===== ラベル比率の観測（DATA BALANCE）[TRAIN] =====
    # align_features_and_labels() 直後：skip除外済み（dropna後）
    total_train = len(y)
    buy_train = int((y == 1).sum())
    sell_train = int((y == 0).sum())
    # skip は dropna() で除外済みのため常に0

    buy_train_pct = (buy_train / total_train * 100.0) if total_train > 0 else 0.0
    sell_train_pct = (sell_train / total_train * 100.0) if total_train > 0 else 0.0

    logger.info(
        "[DATA BALANCE][TRAIN]\n"
        f"  total_samples: {total_train}\n"
        f"  buy:  {buy_train:6d} ({buy_train_pct:5.1f}%)\n"
        f"  sell: {sell_train:6d} ({sell_train_pct:5.1f}%)\n"
        f"  skip: 0 (dropna()で除外済み)\n"
        f"  label_definition:\n"
        f"    buy  = 1 (pips >= min_pips, 上昇)\n"
        f"    sell = 0 (pips <= -min_pips, 下降)\n"
        f"    skip = NaN (変化が小さい、除外済み)\n"
        f"  source:\n"
        f"    file: scripts/weekly_retrain.py\n"
        f"    around: L870 (align_features_and_labels直後)\n"
        f"    label_gen: L262-L280 (build_labels関数)"
    )

    logger.info(
        f"[DATA] X={X.shape} y_pos={buy_train} y_neg={sell_train}"
    )

    if len(X) < 1000:
        logger.warning(
            "[WARN] 学習データが少なすぎます(1000行未満)。処理を中止します。"
        )
        return

    logger.info("[STEP] train_lightgbm_wfo")
    obs_dir = cfg.paths.logs_dir / "retrain" / "lgbm_obs"
    obs_dir.mkdir(parents=True, exist_ok=True)
    logger.info("[OBS][WFO] lgbm_obs output_dir: {}", obs_dir.resolve())
    wfo_result, boosters, oof_pred, final_num_boost_round = train_lightgbm_wfo(
        X, y, rt, obs_output_dir=obs_dir
    )
    logger.info(
        f"[WFO] mean_logloss={wfo_result.mean_logloss:.5f} "
        f"mean_acc={wfo_result.mean_accuracy:.4f}"
    )

    # run_id は epoch 秒（既存の実装に合わせる）
    run_id = int(time.time())

    logger.info("[STEP] optimize_threshold")
    # 既存の動作: min_trades=500（デフォルト）で最適化
    thr_info = optimize_threshold(
        y,
        oof_pred,
        rt.threshold_grid or [],
        wfo_result=wfo_result,
        run_id=run_id,
        min_trades=500,  # 明示的に指定（既存動作維持）
    )

    logger.info("[STEP] save_wfo_report_and_equity")
    # 以降で report 保存などに run_id を使う（既存）
    run_id_str = save_wfo_report_and_equity(
        cfg=cfg,
        df_prices=df_prices,
        X=X,
        y=y,
        oof_pred=oof_pred,
        wfo_result=wfo_result,
        thr_info=thr_info,
        dry_run=dry_run,
        run_id=run_id,
    )
    logger.info(f"[WFO] artifacts saved with run_id={run_id_str}")

    if dry_run:
        logger.info(
            "[DRYRUN] dry-run 指定のためモデル保存/署名は行いません。ここで終了します。"
        )
        return

    logger.info("[STEP] train final model on all data")
    params: dict[str, object] = {
        "objective": "binary",
        "metric": ["binary_logloss"],
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": -1,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "force_col_wise": True,
    }
    train_all = lgb.Dataset(X, label=y)
    booster_all = lgb.train(
        params,
        train_all,
        num_boost_round=final_num_boost_round,
    )

    # 【観測】最終モデルの特徴量重要度（gain/split 上位10）を CSV 保存
    obs_dir = cfg.paths.logs_dir / "retrain" / "lgbm_obs"
    obs_dir.mkdir(parents=True, exist_ok=True)
    fnames_final = booster_all.feature_name()
    gain_final = booster_all.feature_importance(importance_type="gain")
    split_final = booster_all.feature_importance(importance_type="split")
    rows_final: list[dict] = []
    for imp_type, arr in (("gain", gain_final), ("split", split_final)):
        paired = list(zip(fnames_final, arr, strict=True))
        paired.sort(key=lambda x: x[1], reverse=True)
        for feat, imp in paired[:10]:
            rows_final.append({"fold_id": "final", "importance_type": imp_type, "feature": feat, "importance": imp})
    if rows_final:
        fi_final_path = obs_dir / "fi_final.csv"
        pd.DataFrame(rows_final).to_csv(fi_final_path, index=False, encoding="utf-8")
        logger.info("[OBS][WFO] fi_final.csv saved: {}", fi_final_path)

    logger.info("[STEP] save_model_and_meta")
    data_info = {
        "csv_path": str(csv_path),
        "from": df_prices["time"].min().isoformat(),
        "to": df_prices["time"].max().isoformat(),
        "n_rows_raw": int(len(df_prices)),
        "n_rows_train": int(len(X)),
    }
    model_path = save_model_and_meta(
        booster=booster_all,
        cfg=cfg,
        wfo_result=wfo_result,
        threshold_info=thr_info,
        feature_cols=list(X.columns),
        data_info=data_info,
    )

    logger.info(f"[DONE] weekly retrain completed. model={model_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="週次自動再学習 (weekly_retrain)")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="設定ファイルへのパス (default: configs/config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="学習だけ行い、モデル保存や active_model 更新は行わない",
    )
    parser.add_argument(
        "--label-horizon",
        type=int,
        default=None,
        help="ラベル生成のhorizon（設定ファイルの値を上書き）",
    )
    parser.add_argument(
        "--min-pips",
        type=float,
        default=None,
        help="ラベル生成のmin_pips（設定ファイルの値を上書き）",
    )
    args = parser.parse_args()

    # デフォルト候補: configs/config.yaml
    default_config = Path("configs/config.yaml")
    config_path = Path(args.config) if args.config else default_config

    cfg = load_config(config_path)

    # CLI引数で上書き（感度観測用）
    if args.label_horizon is not None:
        cfg.retrain.label_horizon = args.label_horizon
        logger.info(f"[CLI] label_horizon overridden to {args.label_horizon}")
    if args.min_pips is not None:
        cfg.retrain.min_pips = args.min_pips
        logger.info(f"[CLI] min_pips overridden to {args.min_pips}")

    run_weekly_retrain(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
