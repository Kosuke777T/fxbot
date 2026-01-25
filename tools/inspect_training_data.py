# tools/inspect_training_data.py
"""
学習データの検疫レポートツール

特徴量CSV等の学習入力データを解析して、以下の問題を検出：
- 分散ゼロ列（定数列）
- 欠損率
- ラベル比率
- ユニーク数
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

from app.strategies.ai_strategy import build_features_recipe


def inspect_dataframe(df: pd.DataFrame, target_col: str = "target") -> Dict[str, Any]:
    """
    DataFrame を検疫して問題を検出

    Parameters
    ----------
    df : pd.DataFrame
        検疫対象のDataFrame
    target_col : str
        目的変数の列名

    Returns
    -------
    dict
        検疫結果
    """
    result = {
        "n_rows": len(df),
        "n_cols": len(df.columns),
        "columns": [],
        "target_stats": {},
        "issues": [],
    }

    # 各列の統計
    for col in df.columns:
        col_data = df[col]
        col_info: Dict[str, Any] = {
            "name": col,
            "dtype": str(col_data.dtype),
            "n_unique": int(col_data.nunique()),
            "n_missing": int(col_data.isna().sum()),
            "missing_rate": float(col_data.isna().sum() / len(df)) if len(df) > 0 else 0.0,
        }

        # 数値列の場合
        if pd.api.types.is_numeric_dtype(col_data):
            col_info["min"] = float(col_data.min()) if not col_data.isna().all() else None
            col_info["max"] = float(col_data.max()) if not col_data.isna().all() else None
            col_info["mean"] = float(col_data.mean()) if not col_data.isna().all() else None
            col_info["std"] = float(col_data.std()) if not col_data.isna().all() else None

            # 分散ゼロチェック
            if col_info["std"] is not None and col_info["std"] < 1e-10:
                col_info["zero_variance"] = True
                result["issues"].append(f"列 '{col}' は分散ゼロ（定数列）")
            else:
                col_info["zero_variance"] = False

            # NaN/Inf チェック
            n_inf = int(np.isinf(col_data).sum())
            n_nan = int(col_data.isna().sum())
            if n_inf > 0:
                col_info["n_inf"] = n_inf
                result["issues"].append(f"列 '{col}' に {n_inf} 個の Inf 値")
            if n_nan > 0:
                col_info["n_nan"] = n_nan

        # 文字列列の場合
        elif pd.api.types.is_string_dtype(col_data) or pd.api.types.is_object_dtype(col_data):
            col_info["n_unique"] = int(col_data.nunique())
            if col_info["n_unique"] == 1:
                col_info["constant"] = True
                result["issues"].append(f"列 '{col}' は定数（ユニーク値1）")
            else:
                col_info["constant"] = False

        result["columns"].append(col_info)

    # 目的変数の統計
    if target_col in df.columns:
        target_data = df[target_col]
        target_counts = target_data.value_counts()
        result["target_stats"] = {
            "n_total": int(len(target_data)),
            "n_missing": int(target_data.isna().sum()),
            "n_unique": int(target_data.nunique()),
            "value_counts": {str(k): int(v) for k, v in target_counts.items()},
        }

        # ラベル比率
        if len(target_counts) > 0:
            total_valid = target_counts.sum()
            if total_valid > 0:
                ratios = {str(k): float(v / total_valid) for k, v in target_counts.items()}
                result["target_stats"]["ratios"] = ratios

                # 極端な不均衡チェック
                min_ratio = min(ratios.values())
                max_ratio = max(ratios.values())
                if min_ratio < 0.01:
                    result["issues"].append(f"目的変数の不均衡が極端（最小比率: {min_ratio:.3f}）")
                if max_ratio > 0.99:
                    result["issues"].append(f"目的変数がほぼ単一クラス（最大比率: {max_ratio:.3f}）")

    return result


def print_inspection_report(inspection: Dict[str, Any]) -> None:
    """
    検疫レポートを表示

    Parameters
    ----------
    inspection : dict
        検疫結果
    """
    print("=" * 80)
    print("学習データ検疫レポート")
    print("=" * 80)
    print(f"行数: {inspection['n_rows']:,}")
    print(f"列数: {inspection['n_cols']:,}")
    print()

    # 問題の有無
    if inspection["issues"]:
        print("[WARN] 検出された問題:")
        for issue in inspection["issues"]:
            print(f"  - {issue}")
        print()
    else:
        print("[OK] 重大な問題は検出されませんでした")
        print()

    # 列ごとの詳細
    print("列ごとの統計:")
    print(f"{'列名':<20} {'型':<12} {'ユニーク':<10} {'欠損率':<10} {'分散ゼロ':<10} {'備考'}")
    print("-" * 80)
    for col_info in inspection["columns"]:
        name = col_info["name"][:18]
        dtype = col_info["dtype"][:10]
        n_unique = col_info["n_unique"]
        missing_rate = col_info["missing_rate"]
        zero_var = "Yes" if col_info.get("zero_variance", False) else "No"
        notes = ""
        if col_info.get("constant", False):
            notes = "定数"
        elif missing_rate > 0.5:
            notes = f"欠損{missing_rate:.1%}"
        print(f"{name:<20} {dtype:<12} {n_unique:<10} {missing_rate:<10.2%} {zero_var:<10} {notes}")
    print()

    # 目的変数の統計
    if inspection["target_stats"]:
        print("目的変数の統計:")
        stats = inspection["target_stats"]
        print(f"  総数: {stats['n_total']:,}")
        print(f"  欠損: {stats['n_missing']:,}")
        print(f"  ユニーク値数: {stats['n_unique']}")
        if "value_counts" in stats:
            print("  値の分布:")
            for val, count in stats["value_counts"].items():
                ratio = stats.get("ratios", {}).get(val, 0.0)
                print(f"    {val}: {count:,} ({ratio:.2%})")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="学習データ検疫レポートツール")
    parser.add_argument(
        "--data",
        type=Path,
        help="OHLCV CSV のパス（指定時は特徴量を自動生成）",
    )
    parser.add_argument(
        "--features",
        type=Path,
        help="特徴量CSV のパス（直接指定）",
    )
    parser.add_argument(
        "--target-col",
        type=str,
        default="target",
        help="目的変数の列名（デフォルト: target）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="JSON出力パス（指定時はJSON形式で保存）",
    )

    args = parser.parse_args()

    # データ読み込み
    if args.features:
        # 特徴量CSVを直接読み込み
        print(f"[INFO] 特徴量CSV読み込み: {args.features}", flush=True)
        df = pd.read_csv(args.features)
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"])
    elif args.data:
        # OHLCV CSVから特徴量を生成
        print(f"[INFO] OHLCV CSV読み込み: {args.data}", flush=True)
        df_raw = pd.read_csv(args.data)
        df_raw["time"] = pd.to_datetime(df_raw["time"])

        print("[INFO] 特徴量生成中...", flush=True)
        df = build_features_recipe(df_raw, "ohlcv_tech_v1")

        # 目的変数を生成（既存の学習スクリプトと同じロジック）
        LOOKAHEAD = 5
        THRESH_PCT = 0.001
        if "close" in df.columns:
            df["target"] = (df["close"].shift(-LOOKAHEAD) / df["close"] - 1.0 > THRESH_PCT).astype(int)
            df = df.dropna().reset_index(drop=True)
    else:
        parser.error("--data または --features のいずれかを指定してください")

    print(f"[INFO] データ読み込み完了: {len(df):,} 行, {len(df.columns)} 列", flush=True)

    # 検疫実行
    inspection = inspect_dataframe(df, target_col=args.target_col)

    # レポート表示
    print_inspection_report(inspection)

    # JSON出力
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(inspection, f, ensure_ascii=False, indent=2, default=str)
        print(f"[INFO] JSON出力: {output_path}", flush=True)

    # 問題がある場合は exit code 1
    if inspection["issues"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
