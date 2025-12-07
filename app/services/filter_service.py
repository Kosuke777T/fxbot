# app/services/filter_service.py
"""
フィルタサービス

コア層の StrategyFilterEngine を services 層から安全に使うための窓口。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple, List

from app.core.strategy_filter_engine import StrategyFilterEngine


# 単純なシングルトンでOK
_engine: StrategyFilterEngine | None = None


def _get_engine() -> StrategyFilterEngine:
    global _engine
    if _engine is None:
        _engine = StrategyFilterEngine()
    return _engine


def evaluate_entry(entry_context: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Strategy/Execution から呼び出すための窓口。

    Parameters
    ----------
    entry_context : dict
        StrategyFilterEngine が期待する形式の dict。

    Returns
    -------
    ok : bool
        True のときエントリー許可。
    reasons : list[str]
        False のとき NG になった理由一覧。
    """
    from app.services.edition_guard import filter_level
    
    engine = _get_engine()
    level = filter_level()
    ok, reasons = engine.evaluate(entry_context, filter_level=level)
    return ok, reasons

