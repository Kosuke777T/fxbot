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

前提:
- ルート直下 (fxbot/) から実行すること
- 設定: configs/config.yaml もしくは --config で指定
- 価格CSV: data/USDJPY/ohlcv/USDJPY_M5.csv のような構造
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

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
) -> pd.Series:
    """
    horizon 足後の方向ラベルを作る。
    - USDJPY 前提で 1pips = 0.01 として計算。
    - 上昇(min_pips超) = 1, 下降(min_pips超) = 0
      それ以外（変化が小さい）は NaN にして除外。
    """

    future = df["close"].shift(-horizon)
    delta = future - df["close"]
    pips = delta * 100.0  # USDJPY 前提
    y = pd.Series(index=df.index, dtype="float32")
    y[pips >= min_pips] = 1.0
    y[pips <= -min_pips] = 0.0
    return y


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
) -> Tuple[WFOResult, List[lgb.Booster], npt.NDArray[np.float64]]:
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

    oof_pred: npt.NDArray[np.float_] = np.full(
        shape=n,
        fill_value=np.nan,
        dtype="float32",
    )
    boosters: list[lgb.Booster] = []
    fold_results: list[FoldResult] = []

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
            num_boost_round=500,
            valid_sets=[valid_data],
            valid_names=["valid"],
            callbacks=[
                lgb.early_stopping(stopping_rounds=50, verbose=False),
            ],
        )

        boosters.append(booster)

        y_proba: npt.NDArray[np.float_] = booster.predict(
            X_va,
            num_iteration=booster.best_iteration,
        )
        oof_pred[va_idx] = y_proba.astype("float32")

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

        preds_label = (y_proba >= DEFAULT_CLASS_THRESHOLD).astype(int)
        acc = float(((y_va == preds_label).sum()) / len(y_va))

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

    return wfo_result, boosters, oof_pred


# ------------------------
# しきい値最適化
# ------------------------


def optimize_threshold(
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    threshold_grid: list[float],
) -> dict[str, float]:
    """
    非常にシンプルな「1トレード +1 / -1」の疑似損益で最適なしきい値を決める。
    """

    valid_mask = ~np.isnan(oof_pred)
    y_valid_arr: npt.NDArray[np.int_] = y[valid_mask].to_numpy()
    p_valid: npt.NDArray[np.float_] = oof_pred[valid_mask]

    best_thr = DEFAULT_CLASS_THRESHOLD
    best_score = -1e9
    results: list[tuple[float, float, float]] = []

    for thr in threshold_grid:
        trade_mask = p_valid >= thr
        if trade_mask.sum() == 0:
            continue

        y_tr = y_valid_arr[trade_mask]
        pnl = np.where(y_tr == 1, 1.0, -1.0)
        equity = pnl.cumsum()
        total = float(equity[-1])
        winrate = float((pnl > 0).sum() / len(pnl))
        results.append((thr, total, winrate))

        if total > best_score:
            best_score = total
            best_thr = thr

    logger.info(
        "[THR] grid_results="
        + ", ".join(
            f"thr={thr:.3f} total={total:.1f} win={win:.3f}"
            for thr, total, win in results
        )
    )
    logger.info(f"[THR] best_thr={best_thr:.3f} equity={best_score:.1f}")

    return {
        "best_threshold": float(best_thr),
        "best_equity": float(best_score),
    }


def save_wfo_report_and_equity(
    cfg: WeeklyRetrainConfig,
    df_prices: pd.DataFrame,
    X: pd.DataFrame,
    y: pd.Series,
    oof_pred: npt.NDArray[np.float_],
    wfo_result: WFOResult,
    thr_info: dict[str, float],
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
    run_id = str(int(ts.timestamp()))

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
    best_thr = float(thr_info.get("best_threshold", DEFAULT_CLASS_THRESHOLD))

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
    equity_train_path = base_dir / f"equity_train_{run_id}.csv"
    equity_test_path = base_dir / f"equity_test_{run_id}.csv"

    eq_train_df.to_csv(equity_train_path, index=False)
    eq_test_df.to_csv(equity_test_path, index=False)

    logger.info(f"[WFO] equity_train saved: {equity_train_path}")
    logger.info(f"[WFO] equity_test  saved: {equity_test_path}")

    # ---- JSON レポート出力 -------------------------------------------
    report = {
        "run_id": run_id,
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

    report_path = base_dir / f"report_{run_id}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    logger.info(f"[WFO] report saved: {report_path}")

    return run_id


# ------------------------
# モデル保存 & 署名
# ------------------------


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

    dump(booster, model_path)

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
        "best_threshold": threshold_info.get("best_threshold"),
        "feature_order": list(feature_cols),
        "features": list(feature_cols),
    }
    active_path = cfg.paths.models_dir / "active_model.json"
    with active_path.open("w", encoding="utf-8") as f:
        json.dump(active, f, ensure_ascii=False, indent=2)

    logger.info(f"[SAVE] active_model={active_path}")
    return model_path


# ------------------------
# メイン処理
# ------------------------


def run_weekly_retrain(cfg: WeeklyRetrainConfig, dry_run: bool = False) -> None:
    paths = cfg.paths
    rt = cfg.retrain

    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = (
        paths.logs_dir / f"weekly_retrain_{datetime.now().strftime('%Y%m%d')}.log"
    )
    logger.add(log_file, encoding="utf-8")

    logger.info(
        f"[CFG] symbol={rt.symbol} tf={rt.timeframe} label_horizon={rt.label_horizon}"
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
    labels = build_labels(
        df_prices,
        horizon=rt.label_horizon,
        min_pips=rt.min_pips,
    )

    logger.info("[STEP] align_features_and_labels")
    X, y = align_features_and_labels(feats, labels)
    logger.info(
        f"[DATA] X={X.shape} y_pos={int((y == 1).sum())} y_neg={int((y == 0).sum())}"
    )

    if len(X) < 1000:
        logger.warning(
            "[WARN] 学習データが少なすぎます(1000行未満)。処理を中止します。"
        )
        return

    logger.info("[STEP] train_lightgbm_wfo")
    wfo_result, boosters, oof_pred = train_lightgbm_wfo(X, y, rt)
    logger.info(
        f"[WFO] mean_logloss={wfo_result.mean_logloss:.5f} "
        f"mean_acc={wfo_result.mean_accuracy:.4f}"
    )

    logger.info("[STEP] optimize_threshold")
    thr_info = optimize_threshold(y, oof_pred, rt.threshold_grid or [])

    logger.info("[STEP] save_wfo_report_and_equity")
    run_id = save_wfo_report_and_equity(
        cfg=cfg,
        df_prices=df_prices,
        X=X,
        y=y,
        oof_pred=oof_pred,
        wfo_result=wfo_result,
        thr_info=thr_info,
    )
    logger.info(f"[WFO] artifacts saved with run_id={run_id}")

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
    best_iters = [b.best_iteration or 200 for b in boosters]
    num_boost_round = int(np.median(best_iters))
    booster_all = lgb.train(
        params,
        train_all,
        num_boost_round=num_boost_round,
    )

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
    args = parser.parse_args()

    # デフォルト候補: configs/config.yaml
    default_config = Path("configs/config.yaml")
    config_path = Path(args.config) if args.config else default_config

    cfg = load_config(config_path)
    run_weekly_retrain(cfg, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
