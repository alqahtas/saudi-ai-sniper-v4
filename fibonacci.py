"""Fibonacci confluence using two confirmed pivots in trend order."""
from dataclasses import dataclass
from decimal import Decimal as D

RATIOS = (D(".382"), D(".50"), D(".618"))
TOLERANCE = D(".0015")


@dataclass(frozen=True)
class FibonacciConfluence:
    ratio: D
    retracement: D
    extensions: tuple[D, ...]


def confluence(highs, lows, side, entry):
    """Find the latest closed hourly impulse. Both pivots need two closed neighbors."""
    if side not in ("LONG", "SHORT") or len(highs) != len(lows) or len(highs) < 6:
        return None
    high, low = ([D(str(value)) for value in prices] for prices in (highs, lows))
    if any(not h.is_finite() or not l.is_finite() or not 0 < l <= h for h, l in zip(high, low)):
        return None
    entry = D(str(entry))
    if not entry.is_finite() or entry <= 0:
        return None

    pivots = []
    for i in range(2, len(high) - 2):
        neighbors = (i-2, i-1, i+1, i+2)
        top = all(high[i] > high[j] for j in neighbors)
        bottom = all(low[i] < low[j] for j in neighbors)
        if top != bottom:
            pivots.append((i, top, high[i] if top else low[i]))

    long = side == "LONG"
    end = next((i for i in range(len(pivots) - 1, -1, -1) if pivots[i][1] == long), None)
    if end is None:
        return None
    start = next((price for _, top, price in reversed(pivots[:end]) if top != long), None)
    if start is None:
        return None
    index, _, finish = pivots[end]
    if (min(low[index + 1:]) <= start if long else max(high[index + 1:]) >= start):
        return None
    move = finish - start
    if (move > 0) != long or not min(start, finish) < entry < max(start, finish):
        return None

    ratio, level = min(((ratio, finish - move * ratio) for ratio in RATIOS),
                       key=lambda pair: abs(pair[1] - entry))
    if abs(level - entry) > entry * TOLERANCE:
        return None
    extensions = tuple(finish + move * ratio for ratio in RATIOS)
    if min(extensions) <= 0:
        return None
    return FibonacciConfluence(ratio, level, extensions)
