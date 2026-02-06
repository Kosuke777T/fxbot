# app/services/inflight_service.py
# inflight の窓口を services 層に集約。trade_service (PositionGuard) を呼ぶ。
from __future__ import annotations
from typing import Optional
from loguru import logger

from app.core.symbol_map import resolve_symbol


def make_key(symbol: str) -> str:
    """inflight key (A: symbol-only). ENTRY と SLTP/close を同一 inflight 扱いにする。"""
    try:
        return str(resolve_symbol(symbol))
    except Exception:
        return str(symbol or "UNKNOWN")


def mark(key: str, *, intent: Optional[str] = None, ticket: Optional[int] = None) -> None:
    try:
        from app.services import trade_service as _ts
        _ts.mark_order_inflight(key)
    except Exception:
        pass
    if intent == "CLOSE":
        logger.info("[inflight][mark] key={} intent=CLOSE ticket={}", key, ticket)
    else:
        logger.info("[inflight][mark] key={}", key)


def finish(*, key: str, ok: bool, symbol: str, intent: Optional[str] = None, ticket: Optional[int] = None) -> None:
    try:
        from app.services import trade_service as _ts
        _ts.on_order_result(order_id=key, ok=bool(ok), symbol=str(symbol))
    except Exception:
        pass
    if intent == "CLOSE":
        logger.info(
            "[inflight][clear] key={} intent=CLOSE ok={} symbol={} ticket={}",
            key,
            bool(ok),
            symbol,
            ticket,
        )
    else:
        logger.info("[inflight][clear] key={} ok={} symbol={}", key, bool(ok), symbol)
