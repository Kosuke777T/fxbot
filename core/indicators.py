import math
from typing import Sequence


def true_range(h: float, l: float, prev_close: float) -> float:
    """Return Wilder's true range for a single bar."""
    return max(h - l, abs(h - prev_close), abs(prev_close - l))


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int) -> float:
    """Compute a simple average true range over the trailing window."""
    n = len(closes)
    if n < period + 1:
        return math.nan

    trs = []
    for i in range(n - period, n):
        trs.append(true_range(highs[i], lows[i], closes[i - 1]))
    return sum(trs) / len(trs)
