from __future__ import annotations

import time
from app.core.symbol_map import resolve_symbol
from dataclasses import dataclass, field
from typing import Dict, Optional

try:
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover - MT5 unavailable in dryrun/tests
    mt5 = None


@dataclass
class PositionGuardState:
    inflight_orders: Dict[str, float] = field(default_factory=dict)
    last_reconcile_ts: float = 0.0
    open_count: int = 0
    last_fix_reason: Optional[str] = None


class PositionGuard:
    """
    Track live positions/in-flight orders and reconcile with broker state.
    The guard focuses on aggregate count (per account) to keep logic simple.
    """

    def __init__(self, max_positions: int = 1, inflight_timeout_sec: int = 20):
        self.max_positions = max_positions
        self.inflight_timeout_sec = inflight_timeout_sec
        self.state = PositionGuardState()

    # === public API ===

    def mark_inflight(self, order_id: str) -> None:
        """Mark an order as in-flight (before send)."""
        self.state.inflight_orders[str(order_id)] = time.time()

    def clear_inflight(self, order_id: str) -> None:
        """Clear an order from in-flight tracking."""
        self.state.inflight_orders.pop(str(order_id), None)

    def can_open(self) -> bool:
        """Return True when within allowed max positions (after GC)."""
        self._gc_inflight()
        return self.state.open_count < self.max_positions

    def reset(self) -> None:
        """Reset guard state."""
        self.state = PositionGuardState()

    def reconcile_with_broker(self, symbol: Optional[str], desync_fix: bool = True) -> None:
        """
        Sync the local open count with broker positions.
        In dryrun (no MT5), it sticks with current open_count.
        """
        now = time.time()
        self.state.last_reconcile_ts = now
        count = self.state.open_count
        try:
            if mt5 is not None:
                if symbol:
                    poss = mt5.positions_get(symbol=resolve_symbol(symbol)) or []
                else:
                    poss = mt5.positions_get() or []
                count = len(poss)
        except Exception:
            return

        if count != self.state.open_count:
            reason = f"desync(open_count={self.state.open_count} -> {count})"
            self.state.last_fix_reason = reason
            if desync_fix:
                self.state.open_count = count
            self._gc_inflight()

    # === helpers ===

    def _gc_inflight(self) -> None:
        now = time.time()
        dead = [k for k, ts in list(self.state.inflight_orders.items()) if now - ts > self.inflight_timeout_sec]
        for k in dead:
            self.state.inflight_orders.pop(k, None)


# ----------------------------------------------------------------------
# Backwards-compatible procedural helpers (legacy call sites still expect these)
# ----------------------------------------------------------------------
_DEFAULT_GUARD = PositionGuard()


def get_default_guard() -> PositionGuard:
    return _DEFAULT_GUARD


def can_open_new(symbol: Optional[str], max_positions: int) -> bool:
    guard = get_default_guard()
    if guard.max_positions != max_positions:
        guard.max_positions = max_positions
    guard._gc_inflight()
    return guard.can_open()


def mark_inflight(symbol: Optional[str], flag: bool) -> None:
    guard = get_default_guard()
    key = symbol or "GLOBAL"
    if flag:
        guard.mark_inflight(key)
    else:
        guard.clear_inflight(key)


def reset() -> None:
    get_default_guard().reset()


def on_order_rejected_or_canceled(symbol: Optional[str] = None, ticket: Optional[int] = None) -> None:
    mark_inflight(symbol, False)

