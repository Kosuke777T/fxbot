# tools/update_active_model.py
"""
active_model.json を更新するツール

再学習したモデルをSSOTに反映する。
既存のmodel_kind等のフィールドを維持しつつ、file / feature_order / best_threshold を更新する。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.strategies.ai_strategy import load_active_model, get_active_model_meta


def determine_model_kind(model_name: str, file_name: str) -> str:
    """
    model_kind を決定する

    Parameters
    ----------
    model_name : str
        モデル名
    file_name : str
        ファイル名

    Returns
    -------
    str
        model_kind（"builtin" または "pickle"）
    """
    if model_name.startswith("builtin_"):
        return "builtin"
    return "pickle"


def update_active_model(
    model_file: str,
    feature_order: list[str] | None = None,
    best_threshold: float | None = None,
    model_name: str | None = None,
    meta_file: str | None = None,
) -> None:
    """
    active_model.json を更新する

    Parameters
    ----------
    model_file : str
        新しいモデルファイル名
    feature_order : list[str], optional
        特徴量の順序（指定時は上書き、None時は既存を維持）
    best_threshold : float, optional
        最適しきい値（指定時は上書き、None時は既存を維持）
    model_name : str, optional
        モデル名（指定時は上書き、None時は既存を維持）
    meta_file : str, optional
        メタファイル名（指定時は上書き、None時は自動生成）
    """
    active_model_path = PROJECT_ROOT / "models" / "active_model.json"

    # 既存のactive_model.jsonを読み込み
    if active_model_path.exists():
        with open(active_model_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    else:
        meta = {}

    # 既存のmodel_kindを観測・保持
    existing_model_kind = meta.get("model_kind")
    existing_model_name = meta.get("model_name", "LightGBM_clf")

    # 新しいモデルファイルからmodel_kindを決定
    if model_name:
        new_model_name = model_name
    else:
        new_model_name = existing_model_name

    new_model_kind = determine_model_kind(new_model_name, model_file)
    if existing_model_kind is not None:
        # 既存のmodel_kindを優先（既存前提 model_kind != null を満たす）
        model_kind = existing_model_kind
    else:
        # 既存が無い場合は新規決定
        model_kind = new_model_kind

    # 更新
    meta["file"] = model_file
    meta["model_name"] = new_model_name
    if model_kind:
        meta["model_kind"] = model_kind

    if feature_order is not None:
        meta["feature_order"] = feature_order
        meta["features"] = feature_order  # 互換性のため

    if best_threshold is not None:
        meta["best_threshold"] = best_threshold

    # meta_file の更新
    if meta_file:
        meta["meta_file"] = meta_file
    elif model_file.endswith(".pkl"):
        # 自動生成: .pkl -> .pkl.meta.json
        meta["meta_file"] = model_file + ".meta.json"

    # version を更新（タイムスタンプ）
    import time
    meta["version"] = time.time()

    # バックアップ作成
    if active_model_path.exists():
        backup_path = active_model_path.with_suffix(".json.bak")
        import shutil
        shutil.copy2(active_model_path, backup_path)
        print(f"[INFO] バックアップ作成: {backup_path}", flush=True)

    # 保存
    with open(active_model_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"[INFO] active_model.json を更新しました", flush=True)
    print(f"  file: {meta['file']}", flush=True)
    print(f"  model_name: {meta['model_name']}", flush=True)
    if "model_kind" in meta:
        print(f"  model_kind: {meta['model_kind']}", flush=True)
    print(f"  feature_order: {len(meta.get('feature_order', []))} 特徴量", flush=True)
    print(f"  best_threshold: {meta.get('best_threshold', 'N/A')}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="active_model.json 更新ツール")
    parser.add_argument(
        "--model-file",
        type=str,
        required=True,
        help="新しいモデルファイル名（例: LightGBM_clf_20260125_110619.pkl）",
    )
    parser.add_argument(
        "--feature-order",
        type=str,
        help="特徴量の順序（カンマ区切り、またはJSON配列形式）",
    )
    parser.add_argument(
        "--best-threshold",
        type=float,
        help="最適しきい値（デフォルト: 0.5）",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        help="モデル名（デフォルト: 既存を維持）",
    )
    parser.add_argument(
        "--meta-file",
        type=str,
        help="メタファイル名（デフォルト: 自動生成）",
    )
    parser.add_argument(
        "--from-meta",
        type=Path,
        help="モデルのメタファイルから情報を取得（指定時は --feature-order 等を無視）",
    )

    args = parser.parse_args()

    # メタファイルから情報を取得する場合
    if args.from_meta:
        meta_path = Path(args.from_meta)
        if not meta_path.exists():
            print(f"ERROR: メタファイルが見つかりません: {meta_path}", file=sys.stderr)
            sys.exit(1)

        with open(meta_path, "r", encoding="utf-8") as f:
            model_meta = json.load(f)

        feature_order = model_meta.get("feature_order") or model_meta.get("features")
        model_name = model_meta.get("model_name")
        meta_file = meta_path.name if meta_path.name.endswith(".meta.json") else None
    else:
        feature_order = None
        if args.feature_order:
            # カンマ区切りまたはJSON配列形式をパース
            if args.feature_order.startswith("["):
                import json as json_lib
                feature_order = json_lib.loads(args.feature_order)
            else:
                feature_order = [x.strip() for x in args.feature_order.split(",")]

        model_name = args.model_name
        meta_file = args.meta_file

    # 更新実行
    update_active_model(
        model_file=args.model_file,
        feature_order=feature_order,
        best_threshold=args.best_threshold or 0.5,
        model_name=model_name,
        meta_file=meta_file,
    )

    print()
    print("=" * 80)
    print("更新完了")
    print("=" * 80)
    print("次のステップ:")
    print("1. 整合性チェック: python tools/inspect_model_predictions.py --data <data_path>")
    print("2. VirtualBT実行: python tools/backtest_run.py ...")
    print()


if __name__ == "__main__":
    main()
