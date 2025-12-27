from __future__ import annotations

from typing import Any, Dict

from app.services.condition_mining_data import get_decisions_recent_past_summary


def get_decisions_recent_past_min_stats(symbol: str) -> Dict[str, Any]:
    """
    Facade: recent/past の min_stats だけを返す薄いAPI。
    既存の get_decisions_recent_past_summary(symbol) を再利用し、抽出のみを行う。
    """
    try:
        out = get_decisions_recent_past_summary(symbol)
        recent = (out.get("recent") or {})
        past = (out.get("past") or {})
        return {
            "recent": {"min_stats": (recent.get("min_stats") or {})},
            "past": {"min_stats": (past.get("min_stats") or {})},
        }
    except Exception:
        # GUI側を落とさない（縮退）
        return {
            "recent": {"min_stats": {}},
            "past": {"min_stats": {}},
        }
