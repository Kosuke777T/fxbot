from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_JSON = Path("docs/api_catalog.json")
OUT_MD = Path("docs/public_api.md")

ALLOWLIST = Path("docs/public_api_allowlist.txt")
BLOCKLIST = Path("docs/public_api_blocklist.txt")

# ------------------------------------------------------------
# 粒度最適化（ミチビキ仕様 v5/v5.1 寄せ）
#
# Services:
#   - 公開クラス（入口）を class allow し、その public method は基本すべて載せる
#   - 例: ExecutionService / AISvc / KPIService / JobScheduler / RankingService / DiagnosisService
#
# Core:
#   - “契約として固定”したいメソッドだけを method allow（最小限）
#   - 例: StrategyFilterEngine.evaluate / MT5Client の公開API / BacktestEngine.run
#
# allowlist / blocklist は最終的な調整弁として残す
#   - allowlist は blocklist より優先
# ------------------------------------------------------------

# servicesで「クラス単位で公開する」対象
SERVICES_PUBLIC_CLASSES = {
    "ExecutionService",
    "AISvc",
    "KPIService",
    "JobScheduler",
    "RankingService",
    "DiagnosisService",
    # 必要なら追加:
    # "ProfileStatsService",
    # "FilterService",
}

# coreで「メソッド単位で公開する」対象（Class.method）
CORE_PUBLIC_METHODS = {
    "StrategyFilterEngine.evaluate",
    "MT5Client.initialize",
    "MT5Client.order_buy",
    "MT5Client.order_sell",
    "MT5Client.close_position",
    "MT5Client.get_positions",
    "MT5Client.get_price",
    "BacktestEngine.run",
}

@dataclass
class Entry:
    file: str
    lineno: int
    display: str
    doc: str

def load_catalog() -> list[dict[str, Any]]:
    if not CATALOG_JSON.exists():
        raise SystemExit(f"not found: {CATALOG_JSON}  (先に tools/gen_api_catalog.py を実行してください)")
    return json.loads(CATALOG_JSON.read_text(encoding="utf-8"))

def read_list(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s)
    return out

def norm_path(p: str) -> str:
    return (p or "").replace("\\", "/")

def class_name_from_qualname(qualname: str) -> str:
    parts = (qualname or "").split(".")
    return parts[-2] if len(parts) >= 2 else ""

def is_public_name(name: str) -> bool:
    return bool(name) and not name.startswith("_")

def key_variants(item: dict[str, Any]) -> set[str]:
    fp = norm_path(item.get("file", ""))
    name = item.get("name", "")
    qual = item.get("qualname", "")
    cls = class_name_from_qualname(qual)

    keys = set()
    if name:
        keys.add(name)

    if item.get("kind") == "method" and cls and name:
        keys.add(f"{cls}.{name}")

    if fp:
        if item.get("kind") == "method" and cls and name:
            keys.add(f"{fp}:{cls}.{name}")
        elif item.get("kind") == "function" and name:
            keys.add(f"{fp}:{name}")

    return keys

def fmt_signature(item: dict[str, Any]) -> str:
    sig = item.get("signature") or "()"
    ret = item.get("returns") or ""
    r = f" -> {ret}" if ret else ""
    return f"{sig}{r}"

def build_entry(item: dict[str, Any]) -> Entry:
    fp = norm_path(item.get("file", ""))
    ln = int(item.get("lineno", 0) or 0)
    kind = item.get("kind")
    name = item.get("name", "")
    qual = item.get("qualname", "")
    cls = class_name_from_qualname(qual)

    if kind == "method" and cls:
        disp = f"{cls}.{name}{fmt_signature(item)}"
    elif kind == "function":
        disp = f"{name}{fmt_signature(item)}"
    else:
        disp = f"{qual or name}{fmt_signature(item)}"

    doc = (item.get("doc") or "").strip()
    return Entry(file=fp, lineno=ln, display=disp, doc=doc)

def section_name(item: dict[str, Any]) -> str:
    fp = norm_path(item.get("file", ""))
    if fp.startswith("app/services/"):
        return "Services Layer"
    if fp.startswith("app/core/"):
        return "Core Layer"
    return "Other"

def group_key(item: dict[str, Any]) -> str:
    cls = class_name_from_qualname(item.get("qualname", ""))
    return cls or "(top-level)"

def is_selected_by_project_policy(item: dict[str, Any]) -> bool:
    kind = item.get("kind")
    name = item.get("name", "")
    fp = norm_path(item.get("file", ""))

    if kind != "method":
        return False
    if not is_public_name(name):
        return False

    cls = class_name_from_qualname(item.get("qualname", ""))

    # Services: 公開クラスなら public method を全部
    if fp.startswith("app/services/"):
        return cls in SERVICES_PUBLIC_CLASSES

    # Core: メソッド単位で厳選
    if fp.startswith("app/core/"):
        if cls and name:
            return f"{cls}.{name}" in CORE_PUBLIC_METHODS
        return False

    return False

def write_md(by_section: dict[str, dict[str, list[Entry]]], allow: set[str], block: set[str]) -> None:
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Public API\n\n")
    lines.append("このファイルは `tools/gen_public_api.py` により `docs/api_catalog.json` から自動生成されます。\n\n")

    lines.append("## 方針（ミチビキ仕様 寄せ）\n")
    lines.append("- Services は **入口クラス**を公開（public method を列挙）\n")
    lines.append("- Core は **契約として固定したいメソッドのみ**公開（最小限）\n")
    lines.append(f"- allowlist: `{ALLOWLIST}`（強制追加。blockより優先）\n")
    lines.append(f"- blocklist: `{BLOCKLIST}`（強制除外）\n\n")

    lines.append("## Services 公開クラス\n")
    for c in sorted(SERVICES_PUBLIC_CLASSES):
        lines.append(f"- `{c}`\n")
    lines.append("\n## Core 公開メソッド\n")
    for m in sorted(CORE_PUBLIC_METHODS):
        lines.append(f"- `{m}`\n")
    lines.append("\n")

    def dump_section(sec: str):
        lines.append(f"## {sec}\n\n")
        if sec not in by_section or not by_section[sec]:
            lines.append("- (該当なし)\n\n")
            return
        for g in sorted(by_section[sec].keys()):
            lines.append(f"### {g}\n")
            for e in sorted(by_section[sec][g], key=lambda x: (x.file, x.lineno, x.display)):
                doc = f" — {e.doc}" if e.doc else ""
                lines.append(f"- `{e.display}`  ({e.file}:L{e.lineno}){doc}\n")
            lines.append("\n")

    dump_section("Services Layer")
    dump_section("Core Layer")

    lines.append("---\n")
    lines.append(f"- allowlist entries: {len(allow)}\n")
    lines.append(f"- blocklist entries: {len(block)}\n")

    OUT_MD.write_text("".join(lines), encoding="utf-8")

def main() -> int:
    items = load_catalog()
    allow = read_list(ALLOWLIST)
    block = read_list(BLOCKLIST)

    selected_items: list[dict[str, Any]] = []

    for it in items:
        keys = key_variants(it)

        # allow 最優先（blockより優先）
        if allow and (keys & allow):
            selected_items.append(it)
            continue

        # block 除外
        if block and (keys & block):
            continue

        # プロジェクト方針で選別
        if is_selected_by_project_policy(it):
            selected_items.append(it)

    # グルーピング
    by_section: dict[str, dict[str, list[Entry]]] = {}
    for it in selected_items:
        sec = section_name(it)
        grp = group_key(it)
        by_section.setdefault(sec, {}).setdefault(grp, []).append(build_entry(it))

    write_md(by_section, allow, block)
    print(f"written: {OUT_MD}")
    print(f"selected: {sum(len(v2) for v in by_section.values() for v2 in v.values())}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
