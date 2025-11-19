from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional

from app.core import mt5_client
from app.core.config_loader import load_config
from app.services import trade_state
from app.services.circuit_breaker import CircuitBreaker
from app.services.event_store import EVENT_STORE
from core.config import cfg
from core.indicators import atr as _atr
from core.position_guard import PositionGuard
from core.utils.clock import now_jst


@dataclass
class LotRule:
    base_equity_per_0p01: int = 10_000
    min_lot: float = 0.01
    max_lot: float = 1.00
    step: float = 0.01


def round_to_step(x: float, step: float) -> float:
    return (int(x / step)) * step


def calc_lot(equity: float, rule: LotRule = LotRule()) -> float:
    raw = (equity / rule.base_equity_per_0p01) * 0.01
    lot = max(rule.min_lot, min(rule.max_lot, round_to_step(raw, rule.step)))
    return float(f"{lot:.2f}")


def snapshot_account() -> Optional[dict]:
    if not mt5_client.initialize():
        return None
    try:
        return mt5_client.get_account_info()
    finally:
        mt5_client.shutdown()


class TradeService:
    """Facade that coordinates guards, circuit breaker, and decision helpers."""

    def __init__(self) -> None:
        self.pos_guard = PositionGuard()
        self.cb = CircuitBreaker()
        self._reconcile_interval = 15
        self._desync_fix = True
        self._last_reconcile = 0.0
        self.state = trade_state.get_runtime()
        self.reload()

    # ------------------------------------------------------------------ #
    # Configuration & helpers
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        conf = cfg
        g = conf.get("guard", {}) or {}
        cb_cfg = conf.get("circuit_breaker", {}) or {}

        max_positions = int(g.get("max_positions", conf.get("runtime", {}).get("max_positions", 1)))
        inflight_timeout = int(g.get("inflight_timeout_sec", 20))
        self.pos_guard = PositionGuard(max_positions=max_positions, inflight_timeout_sec=inflight_timeout)

        self.cb = CircuitBreaker(
            max_consecutive_losses=int(cb_cfg.get("max_consecutive_losses", conf.get("risk", {}).get("max_consecutive_losses", 5))),
            daily_loss_limit_jpy=float(cb_cfg.get("daily_loss_limit_jpy", 0.0)),
            cooldown_min=int(cb_cfg.get("cooldown_min", 30)),
        )
        self._reconcile_interval = int(g.get("reconcile_interval_sec", 15))
        self._desync_fix = bool(g.get("desync_fix", True))
        self._last_reconcile = 0.0
        self.state = trade_state.get_runtime()

    def _periodic_reconcile(self, symbol: str) -> None:
        now = time.time()
        if now - self._last_reconcile >= self._reconcile_interval:
            self._last_reconcile = now
            self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=self._desync_fix)

    # ------------------------------------------------------------------ #
    # Decisions & guards
    # ------------------------------------------------------------------ #
    def can_open(self, symbol: Optional[str]) -> bool:
        if symbol:
            self._periodic_reconcile(symbol)
        return self.pos_guard.can_open()

    def decide_entry_from_probs(self, p_buy: float, p_sell: float) -> Dict:
        conf = load_config()
        entry_cfg = conf.get("entry", {}) if isinstance(conf, dict) else {}
        th = float(entry_cfg.get("prob_threshold", entry_cfg.get("threshold_buy", 0.60)))
        edge = float(entry_cfg.get("entry_min_edge", entry_cfg.get("min_edge", 0.0)))
        bias = (entry_cfg.get("side_bias") or "auto").lower()

        pmax = p_buy if p_buy >= p_sell else p_sell
        p2nd = p_sell if p_buy >= p_sell else p_buy
        side = "BUY" if p_buy >= p_sell else "SELL"

        if pmax < th:
            return {"decision": "SKIP", "meta": "SKIP", "side": None, "reason": "ai_skip", "threshold": th}

        if (pmax - p2nd) < edge:
            return {
                "decision": "SKIP",
                "meta": "SKIP",
                "side": None,
                "reason": "ai_low_edge",
                "threshold": th,
                "edge": edge,
            }

        if p_buy == p_sell:
            if bias == "buy":
                side = "BUY"
            elif bias == "sell":
                side = "SELL"

        return {"decision": "ENTRY", "meta": side, "side": side, "threshold": th, "edge": edge}

    def decide_entry(self, p_buy: float, p_sell: float) -> Optional[str]:
        result = self.decide_entry_from_probs(p_buy, p_sell)
        return result["side"] if result.get("decision") == "ENTRY" else None

    def can_trade(self) -> bool:
        return self.cb.can_trade()

    def mark_order_inflight(self, order_id: str) -> None:
        self.pos_guard.mark_inflight(order_id)

    def on_order_result(self, *, order_id: str, ok: bool, symbol: str) -> None:
        self.pos_guard.clear_inflight(order_id)
        if ok:
            self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=True)

    def on_order_success(self, *, ticket: Optional[int], side: str, symbol: str, price: Optional[float] = None) -> None:
        self.pos_guard.reconcile_with_broker(symbol=symbol, desync_fix=True)
        runtime = self.state
        runtime.last_ticket = ticket
        runtime.last_side = side
        runtime.last_symbol = symbol
        EVENT_STORE.add(kind="ENTRY", symbol=symbol, side=side, price=price, sl=None, notes=f"ticket={ticket}")

    def on_broker_sync(self, symbol: Optional[str], fix: bool = True) -> None:
        self.pos_guard.reconcile_with_broker(symbol, desync_fix=fix)

    def record_trade_result(
        self,
        *,
        symbol: str,
        side: str,
        profit_jpy: float,
        info: Optional[dict[str, Any]] = None,
    ) -> None:
        resolved_symbol = symbol or self.state.last_symbol or "-"
        resolved_side = side or self.state.last_side
        notes = "settled"
        if info:
            if "notes" in info:
                notes = str(info["notes"])
            else:
                notes = str(info)
        EVENT_STORE.add(
            kind="CLOSE",
            symbol=resolved_symbol,
            side=resolved_side,
            profit_jpy=float(profit_jpy),
            notes=notes,
        )
        self.cb.on_trade_result(profit_jpy)


# ------------------------------------------------------------------ #
# Module-level helpers (backwards compatibility)
# ------------------------------------------------------------------ #
SERVICE = TradeService()


def can_open_new_position(symbol: Optional[str] = None) -> bool:
    settings = trade_state.get_settings()
    if not settings.trading_enabled:
        return False
    sym = symbol or load_config().get("runtime", {}).get("symbol")
    return SERVICE.can_open(sym)


def decide_entry(p_buy: float, p_sell: float) -> Optional[str]:
    return SERVICE.decide_entry(p_buy, p_sell)


def decide_entry_from_probs(p_buy: float, p_sell: float) -> dict:
    return SERVICE.decide_entry_from_probs(p_buy, p_sell)


def get_account_summary() -> dict[str, Any] | None:
    return mt5_client.get_account_info()


def build_exit_plan(symbol: str, ohlc_tail: Optional[Iterable[dict[str, Any]]]) -> dict[str, Any]:
    conf = load_config()
    ex_cfg = conf.get("exits", {}) if isinstance(conf, dict) else {}
    mode = (ex_cfg.get("mode") or "fixed").lower()

    if mode == "none":
        return {"mode": "none"}

    if mode == "fixed":
        fx = ex_cfg.get("fixed", {}) or {}
        return {
            "mode": "fixed",
            "tp_pips": float(fx.get("tp_pips", 10)),
            "sl_pips": float(fx.get("sl_pips", 10)),
        }

    if mode == "atr":
        ax = ex_cfg.get("atr", {}) or {}
        period = int(ax.get("period", 14))
        tp_mult = float(ax.get("tp_mult", 1.2))
        sl_mult = float(ax.get("sl_mult", 1.0))
        trailing = ax.get("trailing", {}) or {}

        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        for row in ohlc_tail or []:
            h = row.get("h") or row.get("high")
            l = row.get("l") or row.get("low")
            c = row.get("c") or row.get("close")
            if h is not None:
                highs.append(float(h))
            if l is not None:
                lows.append(float(l))
            if c is not None:
                closes.append(float(c))

        atr_value = _atr(highs, lows, closes, period)
        return {
            "mode": "atr",
            "atr": atr_value,
            "tp_mult": tp_mult,
            "sl_mult": sl_mult,
            "trailing": {
                "enabled": bool(trailing.get("enabled", True)),
                "activate_atr_mult": float(trailing.get("activate_atr_mult", 0.5)),
                "step_atr_mult": float(trailing.get("step_atr_mult", 0.25)),
                "lock_be_atr_mult": float(trailing.get("lock_be_atr_mult", 0.3)),
                "hard_floor_pips": float(trailing.get("hard_floor_pips", 5)),
                "only_in_profit": bool(trailing.get("only_in_profit", True)),
                "max_layers": int(trailing.get("max_layers", 20)),
                "price_source": (trailing.get("price_source") or "mid").lower(),
            },
        }

    return {"mode": "fixed", "tp_pips": 10, "sl_pips": 10}


_trade_last_fill_ts: Optional[datetime] = None


def mark_filled_now() -> None:
    """Record the timestamp of the latest successful fill."""
    global _trade_last_fill_ts
    _trade_last_fill_ts = now_jst()


def post_fill_grace_active() -> bool:
    """Return True when the post-fill grace window is active."""
    if _trade_last_fill_ts is None:
        return False

    conf = load_config()
    runtime_cfg = conf.get("runtime", {}) if isinstance(conf, dict) else {}
    grace_sec = int((runtime_cfg or {}).get("post_fill_grace_sec", 0) or 0)
    if grace_sec <= 0:
        return False

    return (now_jst() - _trade_last_fill_ts) <= timedelta(seconds=grace_sec)


def mark_order_inflight(order_id: str) -> None:
    SERVICE.mark_order_inflight(order_id)


def on_order_result(order_id: str, ok: bool, symbol: str) -> None:
    SERVICE.on_order_result(order_id=order_id, ok=ok, symbol=symbol)


def reconcile_positions(symbol: Optional[str] = None, desync_fix: bool = True) -> None:
    SERVICE.on_broker_sync(symbol, fix=desync_fix)


def on_order_success(ticket: Optional[int], side: str, symbol: str, price: Optional[float] = None) -> None:
    SERVICE.on_order_success(ticket=ticket, side=side, symbol=symbol, price=price)


def record_trade_result(
    *,
    symbol: str,
    side: str,
    profit_jpy: float,
    info: Optional[dict[str, Any]] = None,
) -> None:
    SERVICE.record_trade_result(symbol=symbol, side=side, profit_jpy=profit_jpy, info=info)


def circuit_breaker_can_trade() -> bool:
    return SERVICE.can_trade()
