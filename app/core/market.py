import MetaTrader5 as mt5
from typing import Any, Optional, Tuple, Dict


def _pip_from_symbol_info(si: Any) -> float:
    # 例: USDJPY なら point=0.001 → pip=0.01（= point*10）
    return float(si.point) * 10.0 if si and si.point else 0.0

# シンボルごとの「最後に取得できた tick(bid, ask)」をキャッシュする
_last_ticks: Dict[str, Tuple[float, float]] = {}

def select_symbol(symbol: str) -> bool:
    si = mt5.symbol_info(symbol)
    if si is None:
        return False
    if not si.visible:
        if not mt5.symbol_select(symbol, True):
            return False
    return True

def spread_pips(symbol: str) -> Optional[float]:
    """
    現在のスプレッドを pips 単位で返す。
    - まず market.tick()（キャッシュ付き）から計算を試みる
    - それでもダメなら symbol_info ベースのフォールバック
    """
    if not select_symbol(symbol):
        return None

    si = mt5.symbol_info(symbol)
    pip = _pip_from_symbol_info(si)
    if pip <= 0:
        return None

    # 1) tick()（キャッシュ付き）から計算
    t = tick(symbol)
    if t is not None:
        bid, ask = t
        return (ask - bid) / pip

    # 2) symbol_info からのフォールバック
    if si and si.ask and si.bid:
        return (float(si.ask) - float(si.bid)) / pip

    if si and si.spread:
        try:
            return (float(si.spread) * float(si.point)) / pip
        except Exception:
            pass

    return None

def tick(symbol: str) -> Optional[Tuple[float, float]]:
    """
    (bid, ask) を返す。
    - 最新 tick が取れればキャッシュを更新して返す
    - MT5 側が一時的に None を返しても、前回のキャッシュがあればそれで復旧する
    """
    if not select_symbol(symbol):
        # シンボルが扱えない場合は、キャッシュも返さない
        return None

    # 最新 tick を MT5 から取得
    t = mt5.symbol_info_tick(symbol)
    if t is not None and getattr(t, 'bid', 0.0) and getattr(t, 'ask', 0.0):
        v = float(t.bid), float(t.ask)
        _last_ticks[symbol] = v
        return v

    # MT5 が None を返した場合や 0 レートの場合でも、
    # 過去のキャッシュがあればそれを返す（None からの復旧）
    return _last_ticks.get(symbol)

def pips_to_price(symbol: str, pips: float) -> Optional[float]:
    if not select_symbol(symbol):
        return None
    si = mt5.symbol_info(symbol)
    pip = _pip_from_symbol_info(si)
    return pips * pip if pip > 0 else None
