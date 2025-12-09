# app/services/loss_streak_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple, Optional


@dataclass
class LossStreakState:
    """プロファイル×シンボルごとの連敗状態"""
    count: int = 0
    last_result_at: Optional[datetime] = None


# key = (profile, symbol)
_state: Dict[Tuple[str, str], LossStreakState] = {}


def get_consecutive_losses(profile: str, symbol: str) -> int:
    """
    現在の連敗数を返す。
    エントリー判断時（EntryContext構築時）に呼び出す。
    """
    key = (profile, symbol)
    state = _state.get(key)
    return state.count if state else 0


def update_on_trade_result(profile: str, symbol: str, pl: float) -> int:
    """
    取引結果を反映して連敗数を更新する。

    pl > 0: 連敗リセット
    pl < 0: 連敗カウント+1
    pl == 0: 変化なし（引き分け扱い）

    戻り値: 更新後の連敗数
    """
    key = (profile, symbol)
    state = _state.get(key)
    if not state:
        state = LossStreakState()
        _state[key] = state

    now = datetime.now()

    if pl > 0:
        state.count = 0
    elif pl < 0:
        state.count += 1
    # pl == 0 はそのまま

    state.last_result_at = now
    return state.count

