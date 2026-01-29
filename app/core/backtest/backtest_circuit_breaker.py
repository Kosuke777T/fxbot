# app/core/backtest/backtest_circuit_breaker.py
"""
バックテスト用サーキットブレーカー（エントリー抑止のみ）。

- live 用 CircuitBreaker(app/services/circuit_breaker.py) とは責務を分離。
- 日次損失は扱わない（バックテストは日跨ぎ・通貨換算など論点が増えるため）。
- 強制クローズは行わない。新規エントリー禁止のみ。
"""
from __future__ import annotations

from typing import Optional


class BacktestCircuitBreaker:
    """
    equity/DD/連敗のしきい値で新規エントリーを止める軽量ブレーカー。
    """

    def __init__(
        self,
        max_drawdown: float,
        max_consecutive_losses: int,
        cooldown_bars: int = 0,
    ) -> None:
        """
        Parameters
        ----------
        max_drawdown : float
            トリップするドローダウン閾値（正の数、例: 0.20 = 20%）。
            dd <= -max_drawdown でトリップ（dd = equity/peak - 1）。
        max_consecutive_losses : int
            トリップする連敗数閾値。consecutive_losses >= でトリップ。
        cooldown_bars : int
            トリップ後、何バー経過で再許可するか。0 の場合は手動解除のみ（未実装のためトリップ後は解除されない）。
        """
        self.max_drawdown = float(max_drawdown)
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.cooldown_bars = int(cooldown_bars)
        self._tripped = False
        self._reason: Optional[str] = None
        self._peak_equity: float = 0.0
        self._last_trip_bar: int = -1

    def update(
        self,
        equity: float,
        peak_equity: float,
        consecutive_losses: int,
        bar_index: int,
    ) -> None:
        """
        現在の状態でピークを更新し、dd を計算してトリップ判定する。
        """
        self._peak_equity = max(self._peak_equity, equity, peak_equity)
        peak = self._peak_equity
        dd = (equity / peak - 1.0) if peak > 0 else 0.0

        if not self._tripped:
            if self.max_drawdown > 0 and dd <= -self.max_drawdown:
                self._tripped = True
                self._reason = "max_drawdown"
                self._last_trip_bar = bar_index
            elif self.max_consecutive_losses > 0 and consecutive_losses >= self.max_consecutive_losses:
                self._tripped = True
                self._reason = "max_consecutive_losses"
                self._last_trip_bar = bar_index

    def can_enter(self, bar_index: int) -> bool:
        """
        新規エントリーしてよいか。tripped かつ cooldown 中でなければ True。
        cooldown_bars > 0 の場合は bar_index ベースで解除可能。
        """
        if not self._tripped:
            return True
        if self.cooldown_bars > 0 and self._last_trip_bar >= 0:
            if (bar_index - self._last_trip_bar) >= self.cooldown_bars:
                self._tripped = False
                self._reason = None
                return True
        return False

    def status(self) -> dict:
        """シリアライズ可能な状態スナップショット。"""
        peak = self._peak_equity
        return {
            "tripped": self._tripped,
            "reason": self._reason,
            "max_drawdown": self.max_drawdown,
            "max_consecutive_losses": self.max_consecutive_losses,
            "cooldown_bars": self.cooldown_bars,
            "last_trip_bar": self._last_trip_bar,
        }
