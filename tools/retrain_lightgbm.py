# tools/retrain_lightgbm.py
"""
LightGBMモデル再学習スクリプト

観測で確定した問題を解決して再学習する：
- 特徴量の一貫性を保つ（active_model.jsonのfeature_orderに合わせる）
- 不均衡データへの対応（class_weight等）
- より多様な予測を生成できるようにする
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.strategies.ai_strategy import (
    build_features_recipe,
    get_active_model_meta,
    validate_feature_order_fail_fast,
)


def load_training_data(
    data_path: Path,
    lookahead: int = 5,
    thresh_pct: float = 0.001,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """
    学習データを読み込む

    Parameters
    ----------
    data_path : Path
        OHLCV CSV のパス
    lookahead : int
        何本先の終値と比較するか
    thresh_pct : float
        上昇判定の閾値（0.1% = 0.001）

    Returns
    -------
    tuple[pd.DataFrame, pd.Series, list[str]]
        (X, y, feature_order)
    """
    print(f"[train] load {data_path}", flush=True)
    df = pd.read_csv(data_path)
    df["time"] = pd.to_datetime(df["time"])

    # 特徴量生成
    print("[train] building features...", flush=True)
    feat = build_features_recipe(df, "ohlcv_tech_v1")

    # 目的変数生成
    feat["target"] = (feat["close"].shift(-lookahead) / feat["close"] - 1.0 > thresh_pct).astype(int)
    feat = feat.dropna().reset_index(drop=True)

    # active_model.json から feature_order を取得
    meta = get_active_model_meta() or {}
    feature_order = meta.get("feature_order") or meta.get("features") or []

    if not feature_order:
        # feature_order が無い場合は、time/close/target を除いた列を使用
        exclude_cols = {"time", "close", "target"}
        feature_order = [c for c in feat.columns if c not in exclude_cols]
        print(f"[train] WARN: feature_order not found in active_model.json, using auto-detected: {len(feature_order)} features", flush=True)
    else:
        # feature_order の検証
        try:
            feature_order = validate_feature_order_fail_fast(
                df_cols=list(feat.columns),
                expected=list(feature_order),
                context="training",
            )
        except RuntimeError as e:
            print(f"[train] ERROR: feature_order validation failed: {e}", flush=True)
            raise

    # 特徴量を feature_order の順序で抽出
    X = feat[feature_order].copy()
    y = feat["target"].copy()

    print(f"[train] samples={len(X):,} features={X.shape[1]} pos_rate={y.mean():.3f}", flush=True)
    print(f"[train] feature_order: {feature_order}", flush=True)

    return X, y, feature_order


def train_model(
    X: pd.DataFrame,
    y: pd.Series,
    random_state: int = 42,
    class_weight: str | dict | None = "balanced",
) -> lgb.LGBMClassifier:
    """
    モデルを学習する

    Parameters
    ----------
    X : pd.DataFrame
        特徴量
    y : pd.Series
        目的変数
    random_state : int
        乱数シード
    class_weight : str | dict | None
        クラス重み（"balanced" で不均衡対応）

    Returns
    -------
    lgb.LGBMClassifier
        学習済みモデル
    """
    print("[train] training model...", flush=True)

    params = dict(
        objective="binary",
        metric="binary_logloss",
        learning_rate=0.05,
        num_leaves=31,
        n_estimators=200,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=random_state,
        class_weight=class_weight,
        verbose=-1,
    )

    model = lgb.LGBMClassifier(**params)
    model.fit(X, y)

    print("[train] model training completed", flush=True)
    return model


def save_model(
    model: lgb.LGBMClassifier,
    feature_order: list[str],
    model_dir: Path,
    timestamp: str,
) -> tuple[Path, Path]:
    """
    モデルを保存する

    Parameters
    ----------
    model : lgb.LGBMClassifier
        学習済みモデル
    feature_order : list[str]
        特徴量の順序
    model_dir : Path
        モデル保存ディレクトリ
    timestamp : str
        タイムスタンプ（ファイル名用）

    Returns
    -------
    tuple[Path, Path]
        (pkl_path, txt_path)
    """
    model_dir.mkdir(exist_ok=True, parents=True)

    # ファイル名生成
    model_name = f"LightGBM_clf_{timestamp}"
    pkl_path = model_dir / f"{model_name}.pkl"
    txt_path = model_dir / f"{model_name}.txt"

    # pkl 保存
    joblib.dump(model, pkl_path, compress=0, protocol=4)
    print(f"[train] saved model (pkl) -> {pkl_path}", flush=True)

    # booster テキスト保存
    try:
        booster = model.booster_
        booster.save_model(str(txt_path))
        print(f"[train] saved model (booster txt) -> {txt_path}", flush=True)
    except Exception as e:
        print(f"[train] WARN: booster save failed: {e}", flush=True)

    # メタデータ保存
    meta_path = model_dir / f"{model_name}.meta.json"
    meta = {
        "model_name": model_name,
        "file": f"{model_name}.pkl",
        "feature_order": feature_order,
        "features": feature_order,
        "version": datetime.now().timestamp(),
        "created_at": datetime.now().isoformat(),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[train] saved metadata -> {meta_path}", flush=True)

    return pkl_path, txt_path


def main() -> None:
    parser = argparse.ArgumentParser(description="LightGBMモデル再学習スクリプト")
    parser.add_argument(
        "--data",
        type=Path,
        default=PROJECT_ROOT / "data" / "USDJPY" / "ohlcv" / "USDJPY_M5.csv",
        help="OHLCV CSV のパス",
    )
    parser.add_argument(
        "--lookahead",
        type=int,
        default=5,
        help="何本先の終値と比較するか（デフォルト: 5）",
    )
    parser.add_argument(
        "--thresh-pct",
        type=float,
        default=0.001,
        help="上昇判定の閾値（デフォルト: 0.001 = 0.1%）",
    )
    parser.add_argument(
        "--random-state",
        type=int,
        default=42,
        help="乱数シード（デフォルト: 42）",
    )
    parser.add_argument(
        "--class-weight",
        type=str,
        default="balanced",
        help="クラス重み（デフォルト: balanced）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "models",
        help="モデル保存ディレクトリ（デフォルト: models/）",
    )

    args = parser.parse_args()

    # データ読み込み
    X, y, feature_order = load_training_data(
        args.data,
        lookahead=args.lookahead,
        thresh_pct=args.thresh_pct,
    )

    # モデル学習
    class_weight = args.class_weight if args.class_weight != "None" else None
    model = train_model(
        X,
        y,
        random_state=args.random_state,
        class_weight=class_weight,
    )

    # モデル保存
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pkl_path, txt_path = save_model(
        model,
        feature_order,
        args.output_dir,
        timestamp,
    )

    print()
    print("=" * 80)
    print("再学習完了")
    print("=" * 80)
    print(f"モデルファイル: {pkl_path}")
    print(f"特徴量数: {len(feature_order)}")
    print(f"特徴量リスト: {feature_order}")
    print()
    print("次のステップ:")
    print("1. 推論値分布を確認: python tools/inspect_model_predictions.py --model <model_path> --data <data_path>")
    print("2. VirtualBTで検証: python tools/backtest_run.py ...")
    print("3. active_model.json を更新（手動）: モデルファイル名とfeature_orderを反映")
    print()


if __name__ == "__main__":
    main()
