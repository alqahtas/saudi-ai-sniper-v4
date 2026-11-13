"""Long-only EMA retest alerts using completed Saudi cash-session candles."""
from dataclasses import dataclass, field
from chart_indicators import analyze as chart_analysis, frame_rows
from datetime import time
import math

import pandas as pd

from fibonacci import FibonacciConfluence, confluence
from saudi_intraday_engine import completed_candles
from timeframe_analysis import _rsi

PERIODS = (25, 75, 140)
MARKET_OPEN = time(10)
MARKET_CLOSE = time(15)
RIYADH = "Asia/Riyadh"


@dataclass(frozen=True)
class EmaRetest:
    entry: float
    stop: float
    strength: float
    confirmed_at: pd.Timestamp
    fib: FibonacciConfluence
    chart_indicators: dict = field(default_factory=dict)


def _session_candles(payload, minutes, now):
    bars = completed_candles(payload, now=now, interval_minutes=minutes)
    if bars.empty or not bars.index.is_unique:
        return bars.iloc[0:0]
    local = bars.index.tz_convert(RIYADH)
    continuous = (local.dayofweek.isin([6, 0, 1, 2, 3])
                  & (local.time >= MARKET_OPEN) & (local.time < MARKET_CLOSE)
                  & (local.minute % minutes == 0) & (local.second == 0))
    return bars.loc[continuous].sort_index()


def _long_stack(closes):
    fast, medium, slow = [closes.ewm(span=n, adjust=False).mean().iloc[-1] for n in PERIODS]
    return fast if fast > medium > slow else None


def _nearest_support(hourly, fifteen, entry):
    supports = []
    for frame in (hourly, fifteen):
        lows = frame["Low"].tail(42).to_list()
        for index in range(2, len(lows) - 2):
            neighbors = lows[index-2:index] + lows[index+1:index+3]
            level = lows[index]
            if 0 < level < entry and level <= min(neighbors) and level < max(neighbors):
                supports.append(level)
    return max(supports, default=None)


def evaluate(hourly_payload, fifteen_payload, quote_price, quote_stamp, now):
    """Confirm the current session's hourly line and latest completed 15m candle."""
    hourly = _session_candles(hourly_payload, 60, now)
    fifteen = _session_candles(fifteen_payload, 15, now)
    if min(len(hourly), len(fifteen)) < max(PERIODS):
        return None
    now = pd.Timestamp(now).tz_convert(RIYADH)
    if not MARKET_OPEN <= now.time() < MARKET_CLOSE:
        return None
    if hourly.index[-1] + pd.Timedelta(hours=1) != now.floor("h"):
        return None
    confirmed = fifteen.index[-1] + pd.Timedelta(minutes=15)
    if confirmed != now.floor("15min"):
        return None
    quote_stamp = pd.Timestamp(quote_stamp)
    if not confirmed <= quote_stamp <= now + pd.Timedelta(seconds=60):
        return None
    if now - quote_stamp > pd.Timedelta(seconds=180):
        return None
    # Ignore a pattern when an in-session candle is missing from the current day.
    for frame, minutes in ((hourly, 60), (fifteen, 15)):
        opened = now.normalize() + pd.Timedelta(hours=MARKET_OPEN.hour)
        expected = pd.date_range(opened, now.floor(f"{minutes}min"), freq=f"{minutes}min",
                                 inclusive="left")
        today = frame.index[frame.index >= opened].tz_convert(RIYADH)
        if not today.equals(expected):
            return None

    hourly_close, close = hourly["Close"], fifteen["Close"]
    entry, fast15 = _long_stack(hourly_close), _long_stack(close)
    if entry is None or fast15 is None or close.iloc[-1] <= max(entry, fast15):
        return None
    strength, previous = _rsi(close), _rsi(close.iloc[:-1])
    if strength is None or previous is None or strength <= max(50, previous):
        return None
    if not math.isfinite(quote_price) or not entry <= quote_price <= entry * 1.005:
        return None
    stop = _nearest_support(hourly, fifteen, entry)
    if stop is None or round(stop, 2) >= round(entry, 2):
        return None
    swing = hourly.tail(42)
    fib = confluence(swing["High"].to_list(), swing["Low"].to_list(), "LONG", format(entry, ".15g"))
    if fib is None:
        return None
    return EmaRetest(entry=entry, stop=stop, strength=strength, confirmed_at=confirmed.tz_convert(RIYADH), fib=fib,
                     chart_indicators={"1h": chart_analysis(frame_rows(hourly)),
                                       "15m": chart_analysis(frame_rows(fifteen))})

