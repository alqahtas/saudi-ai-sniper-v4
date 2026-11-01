"""Original, locally computed technical indicators; no proprietary scripts/data.

OHLCV-based VWAP is an approximation from typical price, not trade-by-trade VWAP.
Volume Profile is an OHLCV approximation, not exchange-executed price-volume.
"""
import numpy as np
import pandas as pd


def professional_indicators(frame, profile_bins=24):
    """Return historical indicator series, never live trading instructions."""
    x = frame.copy().sort_index()
    required = {"High", "Low", "Close", "Volume"}
    if not required.issubset(x.columns):
        raise ValueError("OHLCV fields missing")
    if x.empty:
        raise ValueError("No candles")
    h, l, c, v = (pd.to_numeric(x[k], errors="coerce").astype(float) for k in ("High", "Low", "Close", "Volume"))
    if (v.dropna() < 0).any():
        raise ValueError("Negative volume")
    typical = (h + l + c) / 3
    value = typical * v
    cumulative_volume = v.cumsum().replace(0, np.nan)
    x["vwap_cumulative_approx"] = value.cumsum() / cumulative_volume
    x["avwap_from_first_candle_approx"] = x["vwap_cumulative_approx"]
    prior_close = c.shift()
    tr = pd.concat([(h-l), (h-prior_close).abs(), (l-prior_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["atr14"] = atr
    up = h.diff()
    down = -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    sm_tr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean().replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / sm_tr
    minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / sm_tr
    dx = 100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, np.nan)
    x["adx14"] = dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["plus_di14"] = plus_di
    x["minus_di14"] = minus_di
    x["relative_volume20"] = v / v.shift().rolling(20).mean().replace(0, np.nan)
    x["rsi14"] = 100 - 100 / (1 + c.diff().clip(lower=0).ewm(alpha=1/14, adjust=False).mean() / (-c.diff().clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan))
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    x["macd"] = macd
    x["macd_signal"] = macd.ewm(span=9, adjust=False).mean()
    # Supertrend direction with previous completed bands; no forward-looking candles.
    midpoint = (h+l)/2
    upper = midpoint + 3*atr
    lower = midpoint - 3*atr
    direction = pd.Series(np.nan, index=x.index, dtype=float)
    for i in range(1, len(x)):
        if not np.isfinite(upper.iloc[i-1]) or not np.isfinite(lower.iloc[i-1]):
            continue
        prev = direction.iloc[i-1]
        if c.iloc[i] > upper.iloc[i-1]:
            direction.iloc[i] = 1.0
        elif c.iloc[i] < lower.iloc[i-1]:
            direction.iloc[i] = -1.0
        else:
            direction.iloc[i] = prev if np.isfinite(prev) else 0.0
    x["supertrend_direction_approx"] = direction
    return x


def volume_profile_poc_approx(frame, bins=24):
    """Approximate volume by typical-price bins, not a true exchange volume profile."""
    if not 2 <= bins <= 200:
        raise ValueError("bins out of range")
    h = pd.to_numeric(frame["High"], errors="coerce")
    l = pd.to_numeric(frame["Low"], errors="coerce")
    c = pd.to_numeric(frame["Close"], errors="coerce")
    v = pd.to_numeric(frame["Volume"], errors="coerce")
    price = ((h+l+c)/3).to_numpy(dtype=float)
    volume = v.to_numpy(dtype=float)
    valid = np.isfinite(price) & np.isfinite(volume) & (volume >= 0) & (price > 0)
    if not valid.any() or volume[valid].sum() <= 0:
        return None
    histogram, edges = np.histogram(price[valid], bins=bins, weights=volume[valid])
    k = int(histogram.argmax())
    return float((edges[k]+edges[k+1])/2)
