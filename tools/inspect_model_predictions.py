# tools/inspect_model_predictions.py
"""
モデル推論値の分布確認ツール

学習済みモデルで推論を実行し、prob_buy の分布を分析する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.strategies.ai_strategy import (
    build_features_recipe,
    load_active_model,
    get_active_model_meta,
    _load_model_generic,
    _predict_proba_generic,
    _ensure_feature_order,
    _load_scaler_if_any,
)


def analyze_predictions(
    model_path: Path,
    data_path: Path,
    limit: int = 10000,
) -> Dict[str, Any]:
    """
    モデルで推論を実行し、分布を分析

    Parameters
    ----------
    model_path : Path
        モデルファイルのパス
    data_path : Path
        OHLCV CSV のパス
    limit : int
        読み込む最大行数

    Returns
    -------
    dict
        分析結果
    """
    # データ読み込み
    print(f"[INFO] データ読み込み: {data_path}", flush=True)
    df_raw = pd.read_csv(data_path, nrows=limit)
    df_raw["time"] = pd.to_datetime(df_raw["time"])

    # 特徴量生成
    print("[INFO] 特徴量生成中...", flush=True)
    df_feat = build_features_recipe(df_raw, "ohlcv_tech_v1")
    df_feat = df_feat.dropna().reset_index(drop=True)

    # モデル読み込み
    print(f"[INFO] モデル読み込み: {model_path}", flush=True)
    model = _load_model_generic(str(model_path))

    # active_model.json から設定を取得
    try:
        _, _, _, model_params = load_active_model()
    except Exception:
        model_params = {}

    # 特徴量の順序を確保（active_model.json の feature_order に合わせる）
    # まず feature_order を取得
    try:
        meta = get_active_model_meta() or {}
        feature_order = meta.get("feature_order") or meta.get("features") or []
        if feature_order:
            # feature_order に合わせて列を抽出
            missing = [c for c in feature_order if c not in df_feat.columns]
            if missing:
                print(f"[WARN] 特徴量が不足: {missing}", flush=True)
            X = df_feat[[c for c in feature_order if c in df_feat.columns]].copy()
        else:
            # feature_order が無い場合は _ensure_feature_order を使用
            X = _ensure_feature_order(df_feat, model_params)
    except Exception as e:
        print(f"[WARN] feature_order 取得失敗、フォールバック: {e}", flush=True)
        X = _ensure_feature_order(df_feat, model_params)

    # スケーラー適用
    scaler = _load_scaler_if_any(model_params)
    if scaler is not None:
        Xv = X.values
        try:
            Xv = scaler.transform(Xv)
        except AttributeError:
            if isinstance(scaler, dict) and ("mean" in scaler or "scale" in scaler):
                mean = np.asarray(scaler.get("mean", np.zeros(Xv.shape[1])))
                scale = np.asarray(scaler.get("scale", np.ones(Xv.shape[1])))
                Xv = (Xv - mean) / (scale + 1e-12)
            elif isinstance(scaler, (tuple, list)) and len(scaler) >= 2:
                mean = np.asarray(scaler[0])
                scale = np.asarray(scaler[1])
                Xv = (Xv - mean) / (scale + 1e-12)
            elif isinstance(scaler, np.ndarray):
                mean = scaler
                Xv = (Xv - mean)
        X = pd.DataFrame(Xv, index=X.index, columns=X.columns)

    # 推論実行
    print("[INFO] 推論実行中...", flush=True)
    proba = _predict_proba_generic(model, X)

    # prob_buy を抽出
    if proba.ndim == 2 and proba.shape[1] == 2:
        prob_buy = proba[:, 1]
    else:
        prob_buy = proba.flatten()

    prob_sell = 1.0 - prob_buy

    # 分布分析
    unique_probs = np.unique(prob_buy)
    result = {
        "n_samples": int(len(prob_buy)),
        "prob_buy": {
            "n_unique": int(len(unique_probs)),
            "min": float(np.min(prob_buy)),
            "max": float(np.max(prob_buy)),
            "mean": float(np.mean(prob_buy)),
            "median": float(np.median(prob_buy)),
            "std": float(np.std(prob_buy)),
            "percentiles": {
                "p5": float(np.percentile(prob_buy, 5)),
                "p25": float(np.percentile(prob_buy, 25)),
                "p50": float(np.percentile(prob_buy, 50)),
                "p75": float(np.percentile(prob_buy, 75)),
                "p95": float(np.percentile(prob_buy, 95)),
            },
        },
        "prob_sell": {
            "n_unique": int(len(np.unique(prob_sell))),
            "min": float(np.min(prob_sell)),
            "max": float(np.max(prob_sell)),
            "mean": float(np.mean(prob_sell)),
        },
        "side_distribution": {
            "BUY": int((prob_buy > 0.5).sum()),
            "SELL": int((prob_sell > 0.5).sum()),
            "TIE": int((prob_buy == 0.5).sum()),
        },
    }

    # 値の頻度（Top10）
    value_counts = pd.Series(prob_buy).value_counts().head(10)
    result["prob_buy"]["top_values"] = {
        str(k): int(v) for k, v in value_counts.items()
    }

    return result


def print_analysis_report(analysis: Dict[str, Any]) -> None:
    """
    分析レポートを表示

    Parameters
    ----------
    analysis : dict
        分析結果
    """
    print("=" * 80)
    print("モデル推論値分布分析レポート")
    print("=" * 80)
    print(f"サンプル数: {analysis['n_samples']:,}")
    print()

    # prob_buy の分布
    pb = analysis["prob_buy"]
    print("prob_buy の分布:")
    print(f"  ユニーク値数: {pb['n_unique']:,}")
    print(f"  最小値: {pb['min']:.6f}")
    print(f"  最大値: {pb['max']:.6f}")
    print(f"  平均値: {pb['mean']:.6f}")
    print(f"  中央値: {pb['median']:.6f}")
    print(f"  標準偏差: {pb['std']:.6f}")
    print("  分位点:")
    for k, v in pb["percentiles"].items():
        print(f"    {k}: {v:.6f}")
    print()

    # 頻出値（Top10）
    if "top_values" in pb and pb["top_values"]:
        print("  頻出値（Top10）:")
        for val, count in list(pb["top_values"].items())[:10]:
            pct = count / analysis["n_samples"] * 100
            print(f"    {val}: {count:,} ({pct:.2f}%)")
        print()

    # prob_sell の分布
    ps = analysis["prob_sell"]
    print("prob_sell の分布:")
    print(f"  ユニーク値数: {ps['n_unique']:,}")
    print(f"  最小値: {ps['min']:.6f}")
    print(f"  最大値: {ps['max']:.6f}")
    print(f"  平均値: {ps['mean']:.6f}")
    print()

    # side の分布
    sd = analysis["side_distribution"]
    total = sd["BUY"] + sd["SELL"] + sd["TIE"]
    print("side の分布:")
    print(f"  BUY: {sd['BUY']:,} ({sd['BUY']/total*100:.2f}%)")
    print(f"  SELL: {sd['SELL']:,} ({sd['SELL']/total*100:.2f}%)")
    print(f"  TIE: {sd['TIE']:,} ({sd['TIE']/total*100:.2f}%)")
    print()

    # 評価
    if pb["n_unique"] < 100:
        print("⚠️  警告: prob_buy のユニーク値数が少ない（100未満）")
        print("    → モデルが多様な予測を生成できていない可能性があります")
        print()
    if sd["BUY"] == 0 or sd["SELL"] == 0:
        print("⚠️  警告: BUY または SELL が発生していません")
        print("    → モデルが片方向に偏っている可能性があります")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="モデル推論値分布分析ツール")
    parser.add_argument(
        "--model",
        type=Path,
        help="モデルファイルのパス（指定時は active_model.json を無視）",
    )
    parser.add_argument(
        "--data",
        type=Path,
        required=True,
        help="OHLCV CSV のパス",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="読み込む最大行数（デフォルト: 10000）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON出力パス（指定時はJSON形式で保存）",
    )

    args = parser.parse_args()

    # モデルパス決定
    if args.model:
        model_path = args.model
    else:
        # active_model.json から取得
        try:
            model_kind, model_payload, _, _ = load_active_model()
            if model_kind == "builtin":
                print("ERROR: builtin モデルは分析対象外です", file=sys.stderr)
                sys.exit(1)
            model_path = PROJECT_ROOT / "models" / Path(model_payload).name
            if not model_path.exists():
                model_path = Path(model_payload)
        except Exception as e:
            print(f"ERROR: モデルパス取得失敗: {e}", file=sys.stderr)
            sys.exit(1)

    # 分析実行
    analysis = analyze_predictions(model_path, args.data, limit=args.limit)

    # レポート表示
    print_analysis_report(analysis)

    # JSON出力
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2, default=str)
        print(f"[INFO] JSON出力: {output_path}", flush=True)


if __name__ == "__main__":
    main()
