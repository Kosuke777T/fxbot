import MetaTrader5 as mt5
from typing import Any, Optional, Tuple


def _pip_from_symbol_info(si: Any) -> float:
    # 例: USDJPY なら point=0.001 → pip=0.01（= point*10）
    return float(si.point) * 10.0 if si and si.point else 0.0

def select_symbol(symbol: str) -> bool:
    si = mt5.symbol_info(symbol)
    if si is None:
        return False
    if not si.visible:
        if not mt5.symbol_select(symbol, True):
            return False
    return True

def spread_pips(symbol: str) -> Optional[float]:
    if not select_symbol(symbol):
        return None
    si = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    pip = _pip_from_symbol_info(si)
    if tick and pip > 0:
        return (float(tick.ask) - float(tick.bid)) / pip
    if si and pip > 0 and si.ask and si.bid:
        return (float(si.ask) - float(si.bid)) / pip
    if si and pip > 0 and si.spread:
        try:
            return (float(si.spread) * float(si.point)) / pip
        except Exception:
            pass
    return None

def tick(symbol: str) -> Optional[Tuple[float, float]]:
    """(bid, ask) を返す。取得失敗で None。"""
    if not select_symbol(symbol):
        return None
    t = mt5.symbol_info_tick(symbol)
    if t is None:
        return None
    return float(t.bid), float(t.ask)

def pips_to_price(symbol: str, pips: float) -> Optional[float]:
    if not select_symbol(symbol):
        return None
    si = mt5.symbol_info(symbol)
    pip = _pip_from_symbol_info(si)
    return pips * pip if pip > 0 else None
