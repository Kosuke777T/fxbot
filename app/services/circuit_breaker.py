from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional


JST = timezone(timedelta(hours=9))


@dataclass
class CBState:
    tripped: bool = False
    reason: Optional[str] = None
    consecutive_losses: int = 0
    last_trip_ts: Optional[float] = None
    daily_loss_accum_jpy: float = 0.0
    day_key: str = ""


class CircuitBreaker:
    """
    Resettable circuit breaker that combines consecutive-loss and daily-loss budgets
    with a cool-down period.
    """

    def __init__(
        self,
        max_consecutive_losses: int = 5,
        daily_loss_limit_jpy: float = 0.0,
        cooldown_min: int = 30,
    ):
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.daily_loss_limit_jpy = float(daily_loss_limit_jpy)
        self.cooldown_min = int(cooldown_min)
        self.state = CBState()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def on_trade_result(self, profit_jpy: float) -> None:
        """Record a trade result and trip if thresholds are violated."""
        self._rollover_if_new_day()
        if profit_jpy <= 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0

        self.state.daily_loss_accum_jpy += float(profit_jpy)

        if self.max_consecutive_losses > 0 and self.state.consecutive_losses >= self.max_consecutive_losses:
            self._trip("consecutive_losses")

        if (
            self.daily_loss_limit_jpy
            and self.state.daily_loss_accum_jpy <= -abs(self.daily_loss_limit_jpy)
        ):
            self._trip("daily_loss_limit")

    def can_trade(self) -> bool:
        """Return True if trading is allowed (not tripped or cool-down finished)."""
        if self.state.tripped and self.state.last_trip_ts:
            elapsed = datetime.now(tz=timezone.utc).timestamp() - self.state.last_trip_ts
            if elapsed < self.cooldown_min * 60:
                return False
            self.reset()
        return True

    def reset(self) -> None:
        """Reset trip status (but keep daily accumulator)."""
        self.state.tripped = False
        self.state.reason = None
        self.state.consecutive_losses = 0
        self.state.last_trip_ts = None

    def status(self) -> dict:
        """Return a serialisable snapshot of the breaker state."""
        return {
            "tripped": self.state.tripped,
            "reason": self.state.reason,
            "consecutive_losses": self.state.consecutive_losses,
            "daily_loss_accum_jpy": self.state.daily_loss_accum_jpy,
            "day_key": self.state.day_key,
            "cooldown_min": self.cooldown_min,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _trip(self, reason: str) -> None:
        self.state.tripped = True
        self.state.reason = reason
        self.state.last_trip_ts = datetime.now(tz=timezone.utc).timestamp()

    def _rollover_if_new_day(self) -> None:
        now = datetime.now(JST)
        key = now.strftime("%Y-%m-%d")
        if key != self.state.day_key:
            self.state.day_key = key
            self.state.daily_loss_accum_jpy = 0.0
