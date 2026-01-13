from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from loguru import logger
from app.core import market

JST = timezone(timedelta(hours=9))
TIMEOUT_SECONDS = 0  # 本番寄せ：タイムアウトによる強制クローズを無効化

@dataclass
class MockPosition:
    id: int
    symbol: str
    side: str        # "BUY" or "SELL"
    lot: float
    entry: float
    sl: float
    tp: float
    open_time: datetime = field(default_factory=lambda: datetime.now(JST))
    close_time: Optional[datetime] = None
    closed: bool = False
    close_price: Optional[float] = None
    profit_pips: Optional[float] = None

class OrderBook:
    def __init__(self) -> None:
        self._next_id = 1
        self._positions: List[MockPosition] = []

    def count_open(self, symbol: Optional[str] = None) -> int:
        return sum(1 for p in self._positions if not p.closed and (symbol is None or p.symbol == symbol))

    def open(self, symbol: str, side: str, lot: float, entry: float, sl: float, tp: float) -> MockPosition:
        pos = MockPosition(
            id=self._next_id, symbol=symbol, side=side, lot=lot, entry=entry, sl=sl, tp=tp
        )
        self._next_id += 1
        self._positions.append(pos)
        logger.bind(event="dryrun_open").info({
            "mode":"dryrun", "action":"open", "id": pos.id, "symbol": symbol, "side": side,
            "lot": lot, "entry": entry, "sl": sl, "tp": tp, "ts": pos.open_time.isoformat(timespec="seconds")
        })
        return pos

    def _close(self, p: MockPosition, price: float, reason: str) -> None:
        if p.closed:
            return
        p.closed = True
        p.close_time = datetime.now(JST)
        p.close_price = price
        pip_delta = None
        try:
            fn = getattr(market, "pips_to_price", None)
            pip_delta = fn(p.symbol, 1.0) if callable(fn) else None
        except Exception:
            pip_delta = None
        if pip_delta and pip_delta > 0:
            p.profit_pips = (p.close_price - p.entry)/pip_delta if p.side == "BUY" else (p.entry - p.close_price)/pip_delta
        else:
            p.profit_pips = None

        # --- T-44-3: Exit as Decision (display-only / no behavior change) ---
        # Existing exit conditions in this stub:
        # - TP: take-profit hit -> PROFIT exit
        # - SL: stop-loss hit     -> DEFENSE exit
        # - TIMEOUT/FORCE_CLOSE   -> DEFENSE exit
        exit_reason = str(reason or "UNKNOWN")
        exit_type = "DEFENSE"
        if exit_reason == "TP":
            exit_type = "PROFIT"
        elif exit_reason in ("SL", "TIMEOUT", "FORCE_CLOSE"):
            exit_type = "DEFENSE"
        else:
            exit_type = "DEFENSE"

        # Record into decisions_YYYY-MM-DD.jsonl so EXIT has a readable label.
        # (Do NOT execute any real close orders here; stub already closed internally.)
        try:
            from app.services.execution_stub import _write_decision_log

            ts_str = (
                p.close_time.isoformat(timespec="seconds")
                if p.close_time
                else datetime.now(JST).isoformat(timespec="seconds")
            )
            record = {
                "ts_jst": ts_str,
                "type": "decision",
                "symbol": p.symbol,
                "action": "EXIT",
                "decision": "EXIT",
                "side": p.side,
                "meta": {"source": "orderbook_stub"},
                "decision_detail": {
                    "action": "EXIT",
                    "side": p.side,
                    # add-only
                    "exit_type": exit_type,
                    "exit_reason": exit_reason,
                    "reason": exit_reason,  # legacy single-field reason (readable)
                },
            }
            _write_decision_log(p.symbol, record)
        except Exception:
            # never crash dryrun
            pass
        # --- /T-44-3 ---

        logger.bind(event="dryrun_close").info({
            "mode":"dryrun", "action":"close", "id": p.id, "symbol": p.symbol, "side": p.side,
            "entry": p.entry, "close": p.close_price, "profit_pips": p.profit_pips,
            "reason": reason, "ts": p.close_time.isoformat(timespec="seconds")
        })

    def update_with_market_and_close_if_hit(self, symbol: str) -> None:
        """現在の価格で SL/TP 到達、またはTIMEOUTでクローズ。"""
        tk = market.tick(symbol)
        for p in list(self._positions):
            if p.closed or p.symbol != symbol:
                continue
            # TIMEOUT
            if TIMEOUT_SECONDS and (datetime.now(JST) - p.open_time).total_seconds() >= TIMEOUT_SECONDS:
                price = (tk[1] if p.side == "BUY" else tk[0]) if tk else p.entry
                self._close(p, price, "TIMEOUT")
                continue
            if not tk:
                continue
            bid, ask = tk
            price = ask if p.side == "BUY" else bid
            hit_tp = (price >= p.tp) if p.side == "BUY" else (price <= p.tp)
            hit_sl = (price <= p.sl) if p.side == "BUY" else (price >= p.sl)
            if hit_tp:
                self._close(p, price, "TP")
            elif hit_sl:
                self._close(p, price, "SL")

    def close_all(self, symbol: Optional[str] = None) -> None:
        """現在値で全クローズ（ドライラン）。"""
        tk = None
        if symbol:
            tk = market.tick(symbol)
        for p in list(self._positions):
            if p.closed:
                continue
            if symbol and p.symbol != symbol:
                continue
            price = None
            if tk:
                bid, ask = tk
                price = ask if p.side == "BUY" else bid
            self._close(p, price if price is not None else p.entry, "FORCE_CLOSE")

# シングルトン
_orderbook = OrderBook()
def orderbook() -> OrderBook:
    return _orderbook
