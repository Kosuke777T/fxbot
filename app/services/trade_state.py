from dataclasses import dataclass, asdict
from typing import Any, Optional

@dataclass
class TradeSettings:
    trading_enabled: bool = False
    threshold_buy: float = 0.60
    threshold_sell: float = 0.60
    prob_threshold: float = 0.60
    side_bias: str = "auto"
    tp_pips: int = 15
    sl_pips: int = 10

# シングルトン的にプロセス内共有
_settings = TradeSettings()



@dataclass
class TradeRuntime:
    last_ticket: Optional[int] = None
    last_side: Optional[str] = None
    last_symbol: Optional[str] = None


_runtime_state = TradeRuntime()


def get_runtime() -> TradeRuntime:
    return _runtime_state


def update_runtime(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        if hasattr(_runtime_state, k):
            setattr(_runtime_state, k, v)

def get_settings() -> TradeSettings:
    return _settings

def update(**kwargs: Any) -> None:
    for k, v in kwargs.items():
        if hasattr(_settings, k):
            setattr(_settings, k, v)

def as_dict() -> dict[str, Any]:
    return asdict(_settings)
