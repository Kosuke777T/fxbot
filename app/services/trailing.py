from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TrailConfig:
    pip_size: float
    point: float
    atr: float
    activate_mult: float
    step_mult: float
    lock_be_mult: float
    hard_floor_pips: float
    only_in_profit: bool
    max_layers: int


@dataclass
class TrailState:
    side: str  # "BUY" or "SELL"
    entry: float
    activated: bool = False
    be_locked: bool = False
    layers: int = 0
    current_sl: Optional[float] = None


def _round_to_point(price: float, point: float) -> float:
    k = round(price / point)
    return k * point


def _pips(price_diff: float, pip_size: float) -> float:
    return price_diff / pip_size


def _price_from_pips(pips: float, pip_size: float) -> float:
    return pips * pip_size


def _profit_side(side: str, entry: float, price: float) -> float:
    return (price - entry) if side == "BUY" else (entry - price)


class AtrTrailer:
    def __init__(self, cfg: TrailConfig, state: TrailState):
        self.cfg = cfg
        self.st = state

    def activation_threshold(self) -> float:
        return self.cfg.atr * self.cfg.activate_mult

    def step_size(self) -> float:
        return self.cfg.atr * self.cfg.step_mult

    def be_threshold(self) -> float:
        return self.cfg.atr * self.cfg.lock_be_mult

    def suggest_sl(self, current_price: float) -> Optional[float]:
        profit = _profit_side(self.st.side, self.st.entry, current_price)
        if profit <= 0:
            return None

        if not self.st.activated and profit >= self.activation_threshold():
            self.st.activated = True
            sl = self._hard_floor_sl()
            applied = self._apply_if_better(sl)
            if applied is not None:
                return applied

        if not self.st.activated:
            return None

        if (not self.st.be_locked) and (profit >= self.be_threshold()):
            self.st.be_locked = True
            be_sl = self._breakeven_sl()
            hf_sl = self._hard_floor_sl()
            if self.st.side == "BUY":
                new_sl = max(be_sl, hf_sl)
            else:
                new_sl = min(be_sl, hf_sl)
            return self._apply_if_better(new_sl)

        step = self.step_size()
        if step <= 0:
            return None

        layers_should = int(profit // step)
        layers_should = min(layers_should, self.cfg.max_layers)
        if layers_should <= self.st.layers:
            return None

        move_layers = layers_should - self.st.layers
        new_sl = self._layer_sl(move_layers, current_price)
        self.st.layers = layers_should
        return self._apply_if_better(new_sl)

    def _hard_floor_sl(self) -> float:
        delta = _price_from_pips(self.cfg.hard_floor_pips, self.cfg.pip_size)
        if self.st.side == "BUY":
            sl = self.st.entry + delta
        else:
            sl = self.st.entry - delta
        return _round_to_point(sl, self.cfg.point)

    def _breakeven_sl(self) -> float:
        return _round_to_point(self.st.entry, self.cfg.point)

    def _layer_sl(self, move_layers: int, current_price: float) -> float:
        step = self.step_size() * move_layers
        if self.st.side == "BUY":
            sl = current_price - step
        else:
            sl = current_price + step
        sl = self._ensure_profit_side(sl)
        return _round_to_point(sl, self.cfg.point)

    def _ensure_profit_side(self, sl: float) -> float:
        hf = self._hard_floor_sl()
        current = self.st.current_sl
        if self.st.side == "BUY":
            sl = max(sl, hf)
            if self.cfg.only_in_profit and current is not None:
                sl = max(sl, current)
        else:
            sl = min(sl, hf)
            if self.cfg.only_in_profit and current is not None:
                sl = min(sl, current)
        return sl

    def _apply_if_better(self, new_sl: float) -> Optional[float]:
        cur = self.st.current_sl
        if cur is None:
            self.st.current_sl = new_sl
            return new_sl
        if self.st.side == "BUY" and new_sl > cur:
            self.st.current_sl = new_sl
            return new_sl
        if self.st.side == "SELL" and new_sl < cur:
            self.st.current_sl = new_sl
            return new_sl
        return None
