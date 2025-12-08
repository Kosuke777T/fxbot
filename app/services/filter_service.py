from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.core.strategy_filter_engine import StrategyFilterEngine
from app.services.edition_guard import filter_level

# 型エイリアス（EntryContext 互換）
EntryContext = Dict[str, Any]

# シングルトンインスタンス
_engine: StrategyFilterEngine | None = None


def _get_engine() -> StrategyFilterEngine:
    """
    StrategyFilterEngine のシングルトンを返す。
    デフォルト設定（デフォルトの FilterConfig）で初期化。
    """
    global _engine
    if _engine is None:
        # StrategyFilterEngine 側でデフォルト FilterConfig を持っている想定
        _engine = StrategyFilterEngine()
    return _engine


def evaluate_entry(entry_context: EntryContext) -> Tuple[bool, List[str]]:
    """
    Strategy / Execution から呼び出すための窓口。

    Parameters
    ----------
    entry_context : dict
        エントリー判定に使う情報。
        例:
            {
                "timestamp": datetime,
                "atr": float,
                "volatility": float,
                "trend_strength": float,
                "consecutive_losses": int,
                "profile_stats": dict,
                ...
            }

    Returns
    -------
    ok : bool
        True のときエントリー許可。
    reasons : list[str]
        False のとき NG になった理由一覧。
        True でもチェックされた条件を残したい場合は空リストやヒントを返す。
    """
    engine = _get_engine()
    level = filter_level()
    ok, reasons = engine.evaluate(entry_context, filter_level=level)
    return ok, reasons

