from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.event_store import EVENT_STORE
from core.metrics import METRICS
from core.utils.runtime import is_live

logger = logging.getLogger(__name__)

_mt5svc: Optional[MT5Service] = None

try:
    from app.services.mt5_service import MT5Service
except Exception:
    _mt5svc = None
else:
    _mt5svc = MT5Service(max_retries=3, backoff_sec=0.3, min_change_points=2)


def apply_trailing_update(
    *,
    ticket: Optional[int],
    side: str,
    symbol: str,
    new_sl: float,
    reason: str = "trail",
) -> bool:
    """
    Apply trailing-stop loss updates (dry-run logs or live MT5 OrderModify).
    """

    METRICS.set({"trail_proposed_sl": new_sl, "trail_reason": reason})

    if not is_live():
        EVENT_STORE.add(kind="TRAIL", symbol=symbol, side=side, sl=float(new_sl), reason=reason, notes="DRYRUN")
        logger.info(f"[TRAIL][DRYRUN] side={side} symbol={symbol} new_sl={new_sl} reason={reason}")
        return True

    if _mt5svc is None or ticket is None:
        EVENT_STORE.add(kind="TRAIL", symbol=symbol, side=side, sl=float(new_sl), reason=reason, notes="SKIP")
        logger.warning(
            f"[TRAIL][LIVE][SKIP] ticket={ticket} svc={_mt5svc} side={side} symbol={symbol} new_sl={new_sl}"
        )
        return False

    ok, sent_sl, msg = _mt5svc.safe_order_modify_sl(
        ticket=ticket,
        side=side,
        symbol=symbol,
        desired_sl=new_sl,
        reason=reason,
    )
    sl_val = float(sent_sl) if sent_sl is not None else None

    if ok:
        EVENT_STORE.add(
            kind="TRAIL",
            symbol=symbol,
            side=side,
            sl=sl_val,
            reason=reason,
            notes="OK",
        )
        logger.info(f"[TRAIL][OK] ticket={ticket} side={side} sl={sent_sl} reason={reason} {msg}")
        if sl_val is not None:
            METRICS.set({"trail_current_sl": sl_val})
        METRICS.set({"trail_last_ok": True})
        return True

    if sl_val is not None:
        METRICS.set({"trail_current_sl": sl_val})

    EVENT_STORE.add(kind="TRAIL", symbol=symbol, side=side, sl=float(new_sl), reason=reason, notes="NG")
    logger.warning(f"[TRAIL][NG] ticket={ticket} side={side} desired={new_sl} reason={reason} {msg}")
    METRICS.set({"trail_last_ok": False})
    return False
