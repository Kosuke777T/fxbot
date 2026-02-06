from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
import MetaTrader5 as mt5
from loguru import logger
from app.core.symbol_map import resolve_symbol
from app.core.mt5_client import _resolve_order_send_request
from app.services.inflight_service import make_key as inflight_make_key, mark as inflight_mark, finish as inflight_finish

@dataclass
class BrokerConstraints:
    digits: int
    point: float
    tick_size: float
    stop_level_points: int
    freeze_level_points: int
    trade_stops_level: int
    min_sl_step_points: int = 1

def _symbol_props(symbol: str) -> BrokerConstraints:
    info = mt5.symbol_info(resolve_symbol(symbol))
    if info is None:
        raise RuntimeError(f'symbol_info({symbol}) failed: {mt5.last_error()}')
    stop_level = getattr(info, 'stop_level', 0) or getattr(info, 'trade_stops_level', 0) or 0
    trade_stops = getattr(info, 'trade_stops_level', 0) or stop_level
    freeze = getattr(info, 'freeze_level', 0) or 0
    return BrokerConstraints(digits=info.digits, point=info.point, tick_size=getattr(info, 'trade_tick_size', info.point), stop_level_points=int(stop_level), freeze_level_points=int(freeze), trade_stops_level=int(trade_stops), min_sl_step_points=1)

def _round_to_point(price: float, point: float) -> float:
    return round(price / point) * point

def _sl_min_distance_ok(side: str, price_now: float, sl_price: float, min_points: int, point: float) -> bool:
    dist_points = abs(price_now - sl_price) / point
    if side == 'BUY':
        return sl_price < price_now and dist_points >= min_points
    else:
        return sl_price > price_now and dist_points >= min_points

def _freeze_level_ok(side: str, price_now: float, sl_price: float, freeze_points: int, point: float) -> bool:
    dist_points = abs(price_now - sl_price) / point
    return dist_points > freeze_points

def _snap_sl_to_rules(side: str, price_now: float, desired_sl: float, bc: BrokerConstraints) -> Optional[float]:
    """
    望ましいSLを、StopLevel/FreezeLevel/丸めに収まるよう調整。
    条件を満たせない場合は None（＝更新スキップ）。
    """
    sl = _round_to_point(desired_sl, bc.point)
    if not _sl_min_distance_ok(side, price_now, sl, bc.stop_level_points, bc.point):
        need = bc.stop_level_points - abs(price_now - sl) / bc.point
        steps = int(max(0, need)) + 1
        delta = steps * bc.min_sl_step_points * bc.point
        if side == 'BUY':
            sl = price_now - delta
        else:
            sl = price_now + delta
        sl = _round_to_point(sl, bc.point)
    if not _freeze_level_ok(side, price_now, sl, bc.freeze_level_points, bc.point):
        return None
    if side == 'BUY' and sl >= price_now:
        return None
    if side == 'SELL' and sl <= price_now:
        return None
    return sl

def _price_for_side(tick: Dict[str, float], side: str) -> float:
    if side == 'BUY':
        return tick['bid']
    else:
        return tick['ask']

def _position_of(ticket: int) -> Any:
    pos = mt5.positions_get(ticket=ticket)
    if pos is None:
        raise RuntimeError(f'positions_get failed: {mt5.last_error()}')
    return pos[0] if len(pos) > 0 else None

def _current_tick(symbol: str) -> Dict[str, float]:
    t = mt5.symbol_info_tick(resolve_symbol(symbol))
    if t is None:
        raise RuntimeError(f'symbol_info_tick({symbol}) failed: {mt5.last_error()}')
    return {'bid': t.bid, 'ask': t.ask, 'last': getattr(t, 'last', (t.bid + t.ask) / 2)}

class MT5Service:
    """
    本線：安全なSL更新（OrderModify）
    """

    def __init__(self, max_retries: int=3, backoff_sec: float=0.3, min_change_points: int=2):
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec
        self.min_change_points = min_change_points

    def safe_order_modify_sl(self, ticket: int, side: str, symbol: str, desired_sl: float, reason: str='') -> Tuple[bool, Optional[float], str]:
        """
        返り値: (成功/失敗, 実際に送ったSL, 詳細メッセージ)
        """
        pos = _position_of(ticket)
        if pos is None:
            return (False, None, f'no-position ticket={ticket}')
        bc = _symbol_props(symbol)
        tick = _current_tick(symbol)
        price_now = _price_for_side(tick, side)
        current_sl = float(getattr(pos, 'sl', 0.0) or 0.0)
        if current_sl > 0:
            dpoints = abs(current_sl - desired_sl) / bc.point
            if dpoints < self.min_change_points:
                return (True, None, f'skip: delta<{self.min_change_points}pt (current_sl={current_sl}, desired={desired_sl})')
        snapped = _snap_sl_to_rules(side, price_now, desired_sl, bc)
        if snapped is None:
            return (False, None, f'reject: violates stop/freeze/side rules (desired={desired_sl}, price_now={price_now})')
        # comment: 観測用に intent/ticket を入れる（MT5のcomment制限があるので短くする）
        _c = f"intent=SLTP t={ticket} {reason}".strip()
        request = {'action': mt5.TRADE_ACTION_SLTP, 'position': ticket, 'symbol': symbol, 'sl': round(snapped, bc.digits), 'tp': float(getattr(pos, 'tp', 0.0) or 0.0), 'deviation': 10, 'comment': _c[:28], 'type_time': mt5.ORDER_TIME_GTC, 'type_filling': mt5.ORDER_FILLING_RETURN}
        last_err = ''
        # inflight: A) symbol-only で ENTRY と SLTP を同一 inflight 扱いにする（services の inflight に委譲）
        _inflight_key = inflight_make_key(symbol)
        try:
            inflight_mark(_inflight_key, intent="SLTP", ticket=ticket)
        except Exception:
            pass
        logger.info("[inflight][mark] key={} intent=SLTP ticket={}", _inflight_key, ticket)
        ok = False
        try:
            for i in range(self.max_retries + 1):
                res = mt5.order_send(_resolve_order_send_request(request))
                if res is None:
                    last_err = f'order_send None: {mt5.last_error()}'
                else:
                    if res.retcode == mt5.TRADE_RETCODE_DONE or res.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL:
                        ok = True
                        return (True, request['sl'], f'OK retcode={res.retcode}')
                    last_err = f"retcode={res.retcode}, comment={getattr(res, 'comment', '')}"
                time.sleep(self.backoff_sec)
                tick = _current_tick(symbol)
                price_now = _price_for_side(tick, side)
                snapped = _snap_sl_to_rules(side, price_now, desired_sl, bc)
                if snapped is None:
                    break
                request['sl'] = round(snapped, bc.digits)
            return (False, None, f'fail: {last_err}')
        finally:
            # NOTE: 例外でも inflight が残り続けないように必ず clear（services の inflight に委譲）
            try:
                inflight_finish(key=_inflight_key, ok=ok, symbol=symbol, intent="SLTP", ticket=ticket)
            except Exception:
                pass
            logger.info(
                "[inflight][clear] key={} intent=SLTP ok={} symbol={} ticket={}",
                _inflight_key,
                bool(ok),
                str(symbol),
                ticket,
            )

