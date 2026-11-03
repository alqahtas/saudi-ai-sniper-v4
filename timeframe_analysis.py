"""Pure multi-timeframe analysis for Telegram replies (no network calls)."""
import math

import pandas as pd


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def canonical_ohlcv(frame):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    data = frame.copy()
    aliases = {
        "open": "Open", "o": "Open",
        "high": "High", "h": "High",
        "low": "Low", "l": "Low",
        "close": "Close", "c": "Close",
        "volume": "Volume", "v": "Volume",
    }
    rename = {}
    for column in data.columns:
        key = str(column).strip().lower()
        if key in aliases:
            rename[column] = aliases[key]
    data = data.rename(columns=rename)

    if not isinstance(data.index, pd.DatetimeIndex):
        stamp_column = next(
            (column for column in data.columns if str(column).lower() in
             ("timestamp", "datetime", "date", "time")),
            None,
        )
        if stamp_column is not None:
            data.index = pd.to_datetime(data.pop(stamp_column), errors="coerce", utc=True)
        else:
            data.index = pd.to_datetime(data.index, errors="coerce")

    data = data[~data.index.isna()].sort_index()
    if "Close" not in data:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    data["Close"] = pd.to_numeric(data["Close"], errors="coerce")
    for column in ("Open", "High", "Low"):
        if column not in data:
            data[column] = data["Close"]
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if "Volume" not in data:
        data["Volume"] = 0.0
    data["Volume"] = pd.to_numeric(data["Volume"], errors="coerce").fillna(0.0)
    return data[["Open", "High", "Low", "Close", "Volume"]].dropna(
        subset=["High", "Low", "Close"]
    )


def _rows(payload):
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            return payload
        for item in payload:
            found = _rows(item)
            if found:
                return found
        return []
    if not isinstance(payload, dict):
        return []
    for key in ("candles", "historical", "history", "data", "results", "items", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _rows(value)
            if found:
                return found
    for value in payload.values():
        if isinstance(value, (dict, list)):
            found = _rows(value)
            if found:
                return found
    return []


def _pick(row, names):
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def intraday_frame(payload):
    parsed = []
    for row in _rows(payload):
        if not isinstance(row, dict):
            continue
        stamp = _pick(row, ("timestamp", "datetime", "date", "time", "t"))
        close = _number(_pick(row, ("close", "c")))
        high = _number(_pick(row, ("high", "h")))
        low = _number(_pick(row, ("low", "l")))
        if stamp is None or None in (close, high, low):
            continue
        parsed.append({
            "timestamp": stamp,
            "Open": _number(_pick(row, ("open", "o"))) or close,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": _number(_pick(row, ("volume", "v"))) or 0.0,
        })
    if not parsed:
        return canonical_ohlcv(None)
    return canonical_ohlcv(pd.DataFrame(parsed).set_index("timestamp"))


def resample_ohlcv(frame, rule):
    data = canonical_ohlcv(frame)
    if data.empty:
        return data
    try:
        return data.resample(rule).agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna(subset=["Close"])
    except ValueError:
        if rule == "ME":
            return data.resample("M").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna(subset=["Close"])
        raise


def four_hour_candles(hourly):
    data = canonical_ohlcv(hourly)
    if data.empty:
        return data
    if data.index.tz is not None:
        session_dates = data.index.tz_convert("Asia/Riyadh").date
    else:
        session_dates = data.index.date
    working = data.copy()
    working["_session"] = session_dates
    working["_bucket"] = working.groupby("_session").cumcount() // 4

    rows, indexes = [], []
    for _, group in working.groupby(["_session", "_bucket"], sort=True):
        if len(group) < 4:
            continue
        rows.append({
            "Open": group["Open"].iloc[0],
            "High": group["High"].max(),
            "Low": group["Low"].min(),
            "Close": group["Close"].iloc[-1],
            "Volume": group["Volume"].sum(),
        })
        indexes.append(group.index[-1])
    return canonical_ohlcv(pd.DataFrame(rows, index=indexes)) if rows else canonical_ohlcv(None)


def _rsi(close, period=14):
    if len(close) <= period:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    g, l = _number(avg_gain.iloc[-1]), _number(avg_loss.iloc[-1])
    if g is None or l is None:
        return None
    if l == 0:
        return 50.0 if g == 0 else 100.0
    return 100 - (100 / (1 + g / l))


def summarize_frame(frame):
    data = canonical_ohlcv(frame)
    if len(data) < 15:
        return None
    close = data["Close"]
    current = _number(close.iloc[-1])
    ema20 = _number(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = _number(close.ewm(span=50, adjust=False).mean().iloc[-1])
    rsi = _rsi(close)
    if None in (current, ema20, ema50, rsi):
        return None

    if current > ema20 >= ema50:
        trend = "صاعد"
    elif current < ema20 <= ema50:
        trend = "هابط"
    else:
        trend = "عرضي"

    recent = data.tail(min(20, len(data)))
    support = _number(recent["Low"].min())
    resistance = _number(recent["High"].max())
    if None in (support, resistance):
        return None
    return {
        "trend": trend,
        "rsi": round(rsi, 1),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
    }


def format_timeframe_table(daily, intelligence, opportunity=False):
    data = (intelligence or {}).get("data") or {}
    daily_frame = canonical_ohlcv(daily)

    hourly = intraday_frame(data.get("intraday") or {})
    if len(hourly) > 1:
        hourly = hourly.iloc[:-1]
    four_hour = four_hour_candles(hourly)

    frames = []
    if opportunity:
        fifteen = intraday_frame(data.get("intraday15") or {})
        if len(fifteen) > 1:
            fifteen = fifteen.iloc[:-1]
        frames.extend([
            ("15 دقيقة", fifteen),
            ("ساعة", hourly),
            ("4 ساعات", four_hour),
            ("يومي", daily_frame),
        ])
        title = "🎯 فريمات الفرصة المضاربية"
    else:
        frames.extend([
            ("4 ساعات", four_hour),
            ("يومي", daily_frame),
            ("أسبوعي", resample_ohlcv(daily_frame, "W-THU")),
            ("شهري", resample_ohlcv(daily_frame, "ME")),
        ])
        title = "📊 التحليل متعدد الفريمات"

    rows = []
    for label, frame in frames:
        summary = summarize_frame(frame)
        if summary:
            rows.append(
                f"{label} | {summary['trend']} | {summary['rsi']:.1f} | "
                f"{summary['support']:.2f} | {summary['resistance']:.2f}"
            )
    if not rows:
        return ""
    return "\n".join([
        title,
        "الفريم | الاتجاه | RSI | الدعم | المقاومة",
        *rows,
    ])
