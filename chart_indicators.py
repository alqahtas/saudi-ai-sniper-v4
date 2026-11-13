"""Read-only chart evidence. No I/O, trading, or changes to protection rules.

Retracement uses the displayed 0=low, 1=high convention. Trend extension
requires THREE confirmed alternating pivots: C + (B - A) * ratio.
Callers pass completed candles in chronological order, never a forming bar.
"""
from decimal import Decimal as D, InvalidOperation
import math

EMA_PERIODS = (25, 75, 140)
RETRACEMENT_RATIOS = ("0", "0.236", "0.382", "0.786", "1")
EXTENSION_RATIOS = (*RETRACEMENT_RATIOS, "1.272")
COLORS = {25: "yellow", 75: "pink", 140: "purple"}


def _ema(values, period):
    # TradingView/pandas adjust=False seed; no SMA seed or future samples.
    value = values[0]
    alpha = D(2) / (period + 1)
    for close in values[1:]:
        value += alpha * (close - value)
    return value


def _pivots(rows):
    result = []
    for i in range(2, len(rows) - 2):
        neighbors = [rows[j] for j in (i-2, i-1, i+1, i+2)]
        top = all(rows[i]["high"] > r["high"] for r in neighbors)
        bottom = all(rows[i]["low"] < r["low"] for r in neighbors)
        if top == bottom:
            continue  # An outside bar does not establish an ordered swing.
        point = (i, "high" if top else "low", rows[i]["high" if top else "low"])
        if result and result[-1][1] == point[1]:
            if (point[2] > result[-1][2]) == top:
                result[-1] = point
        else:
            result.append(point)
    return result


def analyze(rows):
    """JSON-safe EMA + Fibonacci context; unavailable data is explicit."""
    try:
        data = [{k: D(str(row[k])) for k in ("high", "low", "close")}
                for row in rows if row.get("complete", True)]
        if any(not all(v.is_finite() and math.isfinite(float(v)) for v in row.values())
               or not 0 < row["low"] <= row["close"] <= row["high"] for row in data):
            return {"status": "invalid_ohlc"}
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return {"status": "invalid_ohlc"}
    out = {"status": "ready" if len(data) >= 140 else "insufficient_ema_history",
           "bars": len(data), "ema": {}, "ema_stack": "UNAVAILABLE",
           "retracement": None, "extension": None, "rebound": None}
    if not data:
        return out
    closes = [r["close"] for r in data]
    if len(data) >= 140:
        values = [_ema(closes, p) for p in EMA_PERIODS]
        out["ema"] = {str(p): float(v) for p, v in zip(EMA_PERIODS, values)}
        a, b, c = values
        out["ema_stack"] = "LONG" if a > b > c else "SHORT" if a < b < c else "NEUTRAL"
    pivots = _pivots(data)
    if len(pivots) < 2:
        return out
    a, b = pivots[-2:]
    low, high = sorted((a[2], b[2]))
    if low == high:
        return out
    # Invalidate after price breaks either end; the levels remain visible only
    # for an intact range. Ratios follow the screenshot, not retracement depth.
    if any(r["low"] < low or r["high"] > high for r in data[b[0]+1:]):
        return out
    levels = {r: low + (high-low)*D(r) for r in RETRACEMENT_RATIOS}
    out["retracement"] = {"anchors": [a[0], b[0]],
                          "levels": {r: float(v) for r, v in levels.items()}}
    long = a[1] == "high"
    directional = levels if long else {r: high-(high-low)*D(r) for r in RETRACEMENT_RATIOS}
    sign = 1 if long else -1
    for ratio in ("0.382", "0.236"):
        trigger = directional[ratio]
        if len(closes) >= 2 and sign*(closes[-2]-trigger) <= 0 < sign*(closes[-1]-trigger):
            target = directional["0.786"]
            if sign*(target-closes[-1]) > 0:
                out["rebound"] = {"side": "LONG" if long else "SHORT", "ratio": ratio,
                                  "trigger": float(trigger), "target_0.786": float(target),
                                  "invalidation": float(b[2])}
            break
    if len(pivots) >= 3:
        a, b, c = pivots[-3:]
        move = b[2]-a[2]
        if min(a[2], b[2]) < c[2] < max(a[2], b[2]):
            values = {r: c[2]+move*D(r) for r in EXTENSION_RATIOS}
            if min(values.values()) > 0:
                out["extension"] = {"anchors": [a[0], b[0], c[0]],
                                    "side": "LONG" if move > 0 else "SHORT",
                                    "levels": {r: float(v) for r, v in values.items()}}
    return out


def frame_context(frames, now_ms):
    """Exclude unfinished/future Binance candles before producing evidence."""
    return {interval: analyze([r for r in rows if r.get("close_time", now_ms) < now_ms])
            for interval, rows in frames.items()}


def frame_rows(frame):
    """Adapt an already completed pandas OHLC frame without importing pandas."""
    return [{"high": r["High"], "low": r["Low"], "close": r["Close"]}
            for _, r in frame.iterrows()]


def describe(context):
    """Compact alert text; never dump a nested payload into a Telegram message."""
    values = context.get("ema", {})
    pieces = ["EMA25/75/140 " + ("/".join(f"{values[str(p)]:.4f}" for p in EMA_PERIODS)
              if values else context.get("status", "unavailable"))]
    for key, label in (("retracement", "Fib 0/23.6/38.2/78.6/100"),
                       ("extension", "Ext A-B-C incl 127.2%")):
        result = context.get(key)
        pieces.append(label + ": " + ("/".join(f"{v:.4f}" for v in result["levels"].values())
                                      if result else "unconfirmed"))
    return " | ".join(pieces)
