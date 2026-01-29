from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

JST = timezone(timedelta(hours=9))


@dataclass
class CBState:
    tripped: bool = False
    reason: Optional[str] = None
    consecutive_losses: int = 0
    last_trip_ts: Optional[float] = None
    # NOTE: 実態は「損失累計」ではなく「損益(PnL)累計」。
    # 勝ちで増え、負けで減る。閾値判定は <= -abs(limit) で日次損失上限として扱う。
    daily_pnl_accum_jpy: float = 0.0
    day_key: str = ""


class CircuitBreaker:
    """
    Resettable circuit breaker that combines consecutive-loss and daily-loss budgets
    with a cool-down period.

    命名: daily_pnl_accum_jpy は「損益(PnL)累計」（勝ちで増え・負けで減る）。
    "loss_accum" だと勝ちで増える挙動が直感に反し運用事故（閾値調整ミス）につながるため pnl_accum に統一。
    判定ロジックはそのまま: pnl_accum <= -abs(daily_loss_limit) で日次損失上限を表現。
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
        self._denial_logged: bool = False  # can_trade() deny 時のログを1回だけ出す用

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

        self.state.daily_pnl_accum_jpy += float(profit_jpy)

        if self.max_consecutive_losses > 0 and self.state.consecutive_losses >= self.max_consecutive_losses:
            self._trip("consecutive_losses")

        if (
            self.daily_loss_limit_jpy
            and self.state.daily_pnl_accum_jpy <= -abs(self.daily_loss_limit_jpy)
        ):
            self._trip("daily_loss_limit")

    def can_trade(self) -> bool:
        """Return True if trading is allowed (not tripped or cool-down finished)."""
        if self.state.tripped and self.state.last_trip_ts:
            elapsed = datetime.now(tz=timezone.utc).timestamp() - self.state.last_trip_ts
            if elapsed < self.cooldown_min * 60:
                if not self._denial_logged:
                    self._denial_logged = True
                    logger.info(
                        "[CB] tripped=True reason={} action=停止(クールダウン中) "
                        "losing_streak={} daily_pnl_accum_jpy={:.0f} "
                        "max_consecutive_losses={} daily_loss_limit_jpy={:.0f} cooldown_min={}",
                        self.state.reason or "circuit_breaker",
                        self.state.consecutive_losses,
                        self.state.daily_pnl_accum_jpy,
                        self.max_consecutive_losses,
                        self.daily_loss_limit_jpy,
                        self.cooldown_min,
                    )
                return False
            self.reset()
        return True

    def reset(self) -> None:
        """Reset trip status (but keep daily accumulator)."""
        self.state.tripped = False
        self.state.reason = None
        self.state.consecutive_losses = 0
        self.state.last_trip_ts = None
        self._denial_logged = False

    def status(self) -> dict:
        """Return a serialisable snapshot of the breaker state."""
        return {
            "tripped": self.state.tripped,
            "reason": self.state.reason,
            "consecutive_losses": self.state.consecutive_losses,
            # 互換のため両方出す（段階的移行）
            "daily_pnl_accum_jpy": self.state.daily_pnl_accum_jpy,
            "daily_loss_accum_jpy": self.state.daily_pnl_accum_jpy,  # DEPRECATED
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
        # 【観測】判定瞬間：現在値・設定値・判定結果を1行で出力（equity/ddは本CBでは未使用）
        logger.info(
            "[CB] tripped=True reason={} action=トリップ(クールダウン開始) "
            "losing_streak={} daily_pnl_accum_jpy={:.0f} day_key={} "
            "max_consecutive_losses={} daily_loss_limit_jpy={:.0f} cooldown_min={}",
            reason,
            self.state.consecutive_losses,
            self.state.daily_pnl_accum_jpy,
            self.state.day_key or "",
            self.max_consecutive_losses,
            self.daily_loss_limit_jpy,
            self.cooldown_min,
        )

    def _rollover_if_new_day(self) -> None:
        now = datetime.now(JST)
        key = now.strftime("%Y-%m-%d")
        if key != self.state.day_key:
            self.state.day_key = key
            self.state.daily_pnl_accum_jpy = 0.0
