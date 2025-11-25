# app/core/symbol_map.py

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

_SYMBOL_MAP_PATH = Path("configs") / "symbols_mt5.json"
_CACHE: Dict[str, str] | None = None


def _load_preferred_map() -> Dict[str, str]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    if not _SYMBOL_MAP_PATH.exists():
        _CACHE = {}
        return _CACHE

    data = json.loads(_SYMBOL_MAP_PATH.read_text(encoding="utf-8"))
    preferred = data.get("preferred", {}) or {}
    # キーも値も str にそろえる
    _CACHE = {str(k): str(v) for k, v in preferred.items()}
    return _CACHE


def resolve_symbol(pair: str) -> str:
    """
    'USDJPY' のような論理ペア名を、実際のMT5シンボル名に解決する。
    マップに無ければ、そのまま返す（後方互換用）。
    """
    mapping = _load_preferred_map()
    return mapping.get(pair, pair)
