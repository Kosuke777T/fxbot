from __future__ import annotations

import ast
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

ROOT = Path(".")
EXCLUDE_DIRS = {".venv", "__pycache__", ".git", "backtests", "logs", "models", "data"}
TARGET_DIRS = ["app", "tools"]  # 必要なら "scripts" も追加

OUT_MD = Path("docs/api_catalog.md")
OUT_JSON = Path("docs/api_catalog.json")

@dataclass
class Item:
    kind: str                 # function | class | method
    name: str
    qualname: str
    file: str
    lineno: int
    signature: str
    returns: str
    doc: str

def _is_excluded(path: Path) -> bool:
    parts = set(path.parts)
    return any(d in parts for d in EXCLUDE_DIRS)

def _ann_to_str(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""

def _arg_to_str(a: ast.arg, default: ast.AST | None) -> str:
    ann = _ann_to_str(a.annotation)
    s = a.arg
    if ann:
        s += f": {ann}"
    if default is not None:
        try:
            s += f"={ast.unparse(default)}"
        except Exception:
            s += "=..."
    return s

def _build_signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str]:
    args = fn.args
    parts: list[str] = []

    # posonly + args
    posonly = getattr(args, "posonlyargs", [])
    defaults = list(args.defaults)
    # defaults apply to last N of (posonlyargs + args)
    normal = list(posonly) + list(args.args)
    n_defaults = len(defaults)
    start_default_at = len(normal) - n_defaults

    for i, a in enumerate(normal):
        d = defaults[i - start_default_at] if i >= start_default_at else None
        parts.append(_arg_to_str(a, d))

    if posonly:
        parts.insert(len(posonly), "/")

    # vararg
    if args.vararg:
        ann = _ann_to_str(args.vararg.annotation)
        s = f"*{args.vararg.arg}"
        if ann:
            s += f": {ann}"
        parts.append(s)
    elif args.kwonlyargs:
        parts.append("*")

    # kwonly
    for i, a in enumerate(args.kwonlyargs):
        d = args.kw_defaults[i]
        parts.append(_arg_to_str(a, d))

    # kwarg
    if args.kwarg:
        ann = _ann_to_str(args.kwarg.annotation)
        s = f"**{args.kwarg.arg}"
        if ann:
            s += f": {ann}"
        parts.append(s)

    sig = f"({', '.join([p for p in parts if p])})"
    ret = _ann_to_str(fn.returns)
    return sig, ret

def _doc_firstline(node: ast.AST) -> str:
    doc = ast.get_docstring(node) or ""
    doc = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    return doc

def scan_file(py: Path) -> list[Item]:
    src = py.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(py))
    items: list[Item] = []

    class_stack: list[str] = []

    def visit(node: ast.AST, parent_class: str | None = None):
        nonlocal items
        if isinstance(node, ast.ClassDef):
            name = node.name
            doc = _doc_firstline(node)
            items.append(Item(
                kind="class",
                name=name,
                qualname=".".join(class_stack + [name]),
                file=str(py).replace("\\", "/"),
                lineno=node.lineno,
                signature="",
                returns="",
                doc=doc,
            ))
            class_stack.append(name)
            for b in node.body:
                visit(b, parent_class=name)
            class_stack.pop()
            return

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig, ret = _build_signature(node)
            doc = _doc_firstline(node)
            if parent_class:
                kind = "method"
                qual = ".".join(class_stack + [node.name])
            else:
                kind = "function"
                qual = node.name
            items.append(Item(
                kind=kind,
                name=node.name,
                qualname=qual,
                file=str(py).replace("\\", "/"),
                lineno=node.lineno,
                signature=sig,
                returns=ret,
                doc=doc,
            ))
            return

        # ignore others

    for n in tree.body:
        visit(n)

    return items

def iter_py_files() -> list[Path]:
    files: list[Path] = []
    for d in TARGET_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if _is_excluded(py):
                continue
            files.append(py)
    return sorted(files)

def write_outputs(items: list[Item]) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    # JSON
    OUT_JSON.write_text(json.dumps([asdict(x) for x in items], ensure_ascii=False, indent=2), encoding="utf-8")

    # MD（ファイル→行順）
    by_file: dict[str, list[Item]] = {}
    for it in items:
        by_file.setdefault(it.file, []).append(it)

    for k in by_file:
        by_file[k].sort(key=lambda x: x.lineno)

    lines: list[str] = []
    lines.append("# API Catalog\n")
    lines.append("このファイルは tools/gen_api_catalog.py により自動生成されます。\n")

    for f in sorted(by_file.keys()):
        lines.append(f"## {f}\n")
        for it in by_file[f]:
            if it.kind == "class":
                lines.append(f"- **class {it.name}**  (L{it.lineno})  — {it.doc}\n")
            elif it.kind == "function":
                ret = f" -> {it.returns}" if it.returns else ""
                lines.append(f"- **def {it.name}{it.signature}{ret}**  (L{it.lineno})  — {it.doc}\n")
            else:  # method
                ret = f" -> {it.returns}" if it.returns else ""
                lines.append(f"  - **{it.qualname}{it.signature}{ret}**  (L{it.lineno})  — {it.doc}\n")
        lines.append("\n")

    OUT_MD.write_text("".join(lines), encoding="utf-8")

def main() -> int:
    items: list[Item] = []
    for py in iter_py_files():
        items.extend(scan_file(py))
    write_outputs(items)
    print(f"written: {OUT_MD}")
    print(f"written: {OUT_JSON}")
    print(f"items  : {len(items)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
