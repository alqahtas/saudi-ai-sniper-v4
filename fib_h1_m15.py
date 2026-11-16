"""Hourly Fibonacci drawings and completed 15m rejection signals. No I/O.

Projection anchors deliberately follow the requested reversal convention:
LONG high-low-high; SHORT low-high-low. C + (B-A)*ratio locates
pullback levels, not conventional continuation targets beyond C.
"""
from dataclasses import dataclass
from decimal import Decimal as D, InvalidOperation

RATIOS = tuple(map(D, ("0", ".236", ".382", ".786", "1")))
PERIODS = (25, 75, 140)
TOLERANCE = D(".0015")
MODEL = "fib_h1_m15_v2"


@dataclass(frozen=True)
class Pivot:
    index: int
    top: bool
    price: D
    confirmed_ms: int


@dataclass(frozen=True)
class Drawing:
    side: str
    anchors: tuple[Pivot, ...]
    retracements: tuple[tuple[D, D], ...]
    projections: tuple[tuple[D, D], ...]


@dataclass(frozen=True)
class Setup:
    side: str
    entry: D
    stop: D
    targets: tuple[D, ...]
    tool: str
    ratio: D
    confirmation_ms: int
    drawing: Drawing
    ema_hour: tuple[D, ...]
    ema_15m: tuple[D, ...]


def candles(rows, duration, now_ms):
    """Discard forming bars, reject malformed/duplicate/overlapping closed bars."""
    result = []
    try:
        for row in rows:
            start, end = int(row["open_time"]), int(row["close_time"])
            if end >= now_ms:
                continue
            prices = {k: D(str(row[k])) for k in ("open", "high", "low", "close", "volume")}
            if (end - start != duration - 1 or start < 0
                    or not all(v.is_finite() for v in prices.values())
                    or prices["volume"] < 0
                    or not 0 < prices["low"] <= min(prices["open"], prices["close"])
                    <= max(prices["open"], prices["close"]) <= prices["high"]
                    or (result and start <= result[-1]["close_time"])):
                return []
            result.append(dict(prices, open_time=start, close_time=end))
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        return []
    return result


def pivots(rows):
    """Two closed bars on each side; compress consecutive pivots of the same kind."""
    found = []
    for i in range(2, len(rows) - 2):
        neighbors = [rows[j] for j in (i-2, i-1, i+1, i+2)]
        top = all(rows[i]["high"] > r["high"] for r in neighbors)
        bottom = all(rows[i]["low"] < r["low"] for r in neighbors)
        if top == bottom:
            continue
        p = Pivot(i, top, rows[i]["high" if top else "low"], rows[i+2]["close_time"])
        if found and found[-1].top == p.top:
            if (p.price > found[-1].price) == p.top:
                found[-1] = p
        else:
            found.append(p)
    return found


def drawing(rows, side):
    if side not in ("LONG", "SHORT"):
        return None
    points = pivots(rows)
    top = side == "LONG"
    end = next((i for i in range(len(points)-1, 1, -1) if points[i].top == top), None)
    if end is None:
        return None
    a, b, c = points[end-2:end+1]
    sign = 1 if top else -1
    if min(sign*(a.price-b.price), sign*(c.price-b.price)) <= 0:
        return None
    # A new move through B invalidates this reversal setup.
    after = rows[c.index+1:]
    if any(sign*(r["low" if top else "high"]-b.price) <= 0 for r in after):
        return None
    retr = tuple((r, c.price-(c.price-b.price)*r) for r in RATIOS)
    projected = tuple((r, c.price+(b.price-a.price)*r) for r in RATIOS)
    if any(price <= 0 for _, price in retr+projected):
        return None
    return Drawing(side, (a,b,c), retr, projected)


def ema(values, period):
    value, alpha = values[0], D(2)/D(period+1)
    for price in values[1:]:
        value += alpha*(price-value)
    return value


def stack(rows):
    closes = [r["close"] for r in rows]
    values = tuple(ema(closes, n) for n in PERIODS)
    return ("LONG" if values[0] > values[1] > values[2] else
            "SHORT" if values[0] < values[1] < values[2] else None), values


def select_setup(hourly, fifteen, now_ms):
    """A touch alone never enters: latest completed 15m must reject a known level.

    Callers apply market hours, quotes, tick sizes, fees, risk/reward and cooldown.
    Targets are the next levels in the PROFIT direction from the entry, regardless
    of projection direction. No 50/61.8/161.8 levels are silently introduced.
    """
    hour = candles(hourly, 3_600_000, now_ms)
    lower = candles(fifteen, 900_000, now_ms)
    if min(len(hour), len(lower)) < max(PERIODS):
        return None, "fib_history"
    if (lower[-1]["close_time"] != now_ms//900_000*900_000-1
            or now_ms-hour[-1]["close_time"] > 3_600_000
            or any(b["open_time"]-a["open_time"] != 900_000 for a,b in zip(lower[-4:],lower[-3:]))):
        return None, "fib_stale_or_gapped"
    side, hour_ema = stack(hour)
    lower_side, lower_ema = stack(lower)
    if side is None or side != lower_side:
        return None, "fib_ema_alignment"
    sign = 1 if side == "LONG" else -1
    last = lower[-1]
    if min(sign*(last["close"]-last["open"]), sign*(last["close"]-lower_ema[0])) <= 0:
        return None, "fib_15m_rejection"
    # Build levels only with hourly candles already closed when this 15m began.
    known_hour = [r for r in hour if r["close_time"] < last["open_time"]]
    chart = drawing(known_hour, side)
    if chart is None:
        return None, "fib_three_pivots"
    all_levels = [("retracement", r, p) for r,p in chart.retracements]
    all_levels += [("projection", r, p) for r,p in chart.projections]
    touched = [(tool,r,p) for tool,r,p in all_levels
               if last["low"] <= p*(1+TOLERANCE) and last["high"] >= p*(1-TOLERANCE)
               and sign*(last["close"]-p) > 0]
    touched.sort(key=lambda x: abs(last["close"]-x[2]))
    structure = [p for rows in (known_hour,lower[:-1]) for p in pivots(rows)]
    stops = [p.price for p in structure if p.top != (side=="LONG")]
    for tool,ratio,entry in touched:
        structural = [p for p in stops if sign*(entry-p) > 0
                      and sign*(last["low" if side=="LONG" else "high"]-p) > 0]
        targets = sorted({p for _,_,p in all_levels if sign*(p-entry) > entry*D("1e-12")},
                         reverse=side=="SHORT")
        if not structural or not targets:
            continue
        barriers = [p.price for p in structure if p.top == (side=="LONG")
                    and sign*(p.price-entry) > 0]
        if any(sign*(barrier-targets[0]) < 0 for barrier in barriers):
            continue
        stop = max(structural) if side == "LONG" else min(structural)
        return Setup(side, entry, stop, tuple(targets), tool, ratio, last["close_time"],
                     chart, hour_ema, lower_ema), "approved"
    return None, "fib_no_rejection_level"
