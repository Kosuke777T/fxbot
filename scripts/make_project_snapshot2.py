#!/usr/bin/env python
"""
プロジェクト全体のフォルダ構造と主要ファイルの内容を
1つの project_snapshot.txt にまとめて出力するスクリプト。

- プロジェクトルート = このファイルの 1 つ上のフォルダ
- 除外ディレクトリや対象拡張子は CONFIG のところで調整可能
- ディレクトリツリーにはファイルの最終更新日時も表示します
"""

from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime
from typing import Iterable, List

# ========================
# 設定
# ========================

# 除外するディレクトリ名（部分一致）
EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "logs",
    "data",
    "models",
    "設定",
    "dist",
    "build",
    ".ruff_cache",
}

# 中身を書き出す対象の拡張子
INCLUDE_EXTENSIONS = {
    ".py",
}

# ファイルサイズの上限（バイト）。これを超えると中身はスキップしてヘッダだけ書く
MAX_FILE_SIZE_BYTES = 100_000  # 100KB

# 出力ファイル名
SNAPSHOT_FILENAME = "project_snapshot.txt"


# ========================
# ヘルパー関数
# ========================

def iter_tree(root: Path) -> Iterable[Path]:
    """root 配下のファイル・ディレクトリを walk するジェネレータ。
    除外ディレクトリをスキップする。
    """
    for dirpath, dirnames, filenames in os.walk(root):
        # dirnames を in-place でフィルタすると os.walk がそれを辿らなくなる
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIR_NAMES
        ]
        current_dir = Path(dirpath)
        # ディレクトリ自身
        yield current_dir
        # ファイル
        for fname in filenames:
            yield current_dir / fname


def make_tree_text(root: Path) -> str:
    """ディレクトリツリー文字列を生成する（ファイルには最終更新日時付き）。"""
    lines: List[str] = []

    root_str = root.name
    lines.append(f"{root_str}/")

    # root からの相対パスでソートして表示
    paths = sorted(
        (p for p in iter_tree(root)),
        key=lambda p: str(p.relative_to(root)).lower(),
    )

    seen_dirs = set()

    for p in paths:
        rel = p.relative_to(root)
        parts = rel.parts

        # ルート自身はもう書いているのでスキップ
        if rel == Path("."):
            continue

        indent = "  " * (len(parts) - 1)
        name = parts[-1]

        if p.is_dir():
            # ディレクトリ
            dir_key = rel
            if dir_key in seen_dirs:
                continue
            seen_dirs.add(dir_key)
            lines.append(f"{indent}{name}/")
        else:
            # ファイル（最終更新日時を付ける）
            if p.suffix.lower() != ".py":
                continue
            try:
                mtime = datetime.fromtimestamp(p.stat().st_mtime)
                mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                mtime_str = "unknown"
            lines.append(f"{indent}{name} (updated: {mtime_str})")

    return "\n".join(lines)


def should_dump_content(path: Path) -> bool:
    """このファイルの中身をスナップショットに含めるか判定。"""
    if not path.is_file():
        return False

    if path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False

    try:
        size = path.stat().st_size
    except OSError:
        return False

    if size > MAX_FILE_SIZE_BYTES:
        return False

    return True


def read_text_safely(path: Path) -> str:
    """UTF-8 で読みつつ、読めない文字は置き換える。"""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"<< FAILED TO READ FILE: {e} >>"


def collect_files_for_dump(root: Path) -> List[Path]:
    """中身を抜き出す対象のファイル一覧を返す。"""
    files: List[Path] = []
    for p in iter_tree(root):
        if p.is_file() and should_dump_content(p):
            files.append(p)
    files.sort(key=lambda p: str(p.relative_to(root)).lower())
    return files


# ========================
# メイン処理
# ========================

def main() -> int:
    # このスクリプトの 1 つ上をプロジェクトルートとみなす
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent
    snapshot_path = project_root / SNAPSHOT_FILENAME

    print(f"[INFO] script_path   = {script_path}")
    print(f"[INFO] project_root  = {project_root}")
    print(f"[INFO] snapshot_path = {snapshot_path}")

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1) ヘッダ
    header_lines = [
        "#" * 60,
        "# project_snapshot",
        "#" * 60,
        f"generated_at: {now}",
        f"project_root: {project_root}",
        "",
        "NOTE:",
        "  - このファイルは ChatGPT にプロジェクト構造と主要ファイルを伝えるためのスナップショットです。",
        "  - ログ・データ・モデル・.git・.venv などは除外しています。",
        "  - ディレクトリツリーにはファイルごとの最終更新日時 (updated: ...) を含みます。",
        "",
        "========================================",
        "=== DIRECTORY TREE =====================",
        "========================================",
        "",
    ]

    tree_text = make_tree_text(project_root)

    # 2) ファイル内容
    files = collect_files_for_dump(project_root)

    content_lines: List[str] = []
    content_lines.append("")
    content_lines.append("")
    content_lines.append("========================================")
    content_lines.append("=== FILE CONTENTS ======================")
    content_lines.append("========================================")
    content_lines.append("")

    for fpath in files:
        rel = fpath.relative_to(project_root)
        content_lines.append("")
        content_lines.append(f"=== file: {rel.as_posix()} ===")
        content_lines.append("")
        txt = read_text_safely(fpath)
        content_lines.append(txt)
        content_lines.append("")  # 区切り

    # 3) スナップショットを書き出し
    all_text = "\n".join(header_lines) + "\n" + tree_text + "\n" + "\n".join(content_lines)

    try:
        snapshot_path.write_text(all_text, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] failed to write snapshot: {e}")
        return 1

    print(f"[OK] snapshot written: {snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
