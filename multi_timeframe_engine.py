
"""Saudi AI Sniper - Multi Timeframe Engine V1.

Analyzes daily, weekly and monthly candles.
Research only. No orders or Telegram alerts.
"""

import json
import sys
from pathlib import Path

import pandas as pd

from sniper_core import daily


def completed_bars(frame, rule):
    """Resample and exclude unfinished periods."""

    x = frame.copy().sort_index()
    idx = pd.DatetimeIndex(x.index)

    if idx.tz is not None:
        idx = idx.tz_localize(None)

    x.index = idx

    aggregation = {
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }

    if rule == "weekly":
        # Saudi exchange weeks end on Thursday.
        grouped = x.resample("W-THU").agg(
            aggregation
        ).dropna()

        period_end = (
            x.index[-1]
            .to_period("W-THU")
            .end_time.normalize()
        )
    else:
        grouped = x.resample("ME").agg(
            aggregation
        ).dropna()

        period_end = (
            x.index[-1]
            .to_period("M")
            .end_time.normalize()
        )

    today = (
        pd.Timestamp.now(tz="Asia/Riyadh")
        .tz_localize(None)
        .normalize()
    )

    if len(grouped) and today <= period_end:
        grouped = grouped.iloc[:-1]

    return grouped


def pivots(x, span=2):
    """Find confirmed historical swing highs and lows."""

    highs = x["High"]
    lows = x["Low"]

    pivot_highs = []
    pivot_lows = []

    for i in range(span, len(x) - span):
        window_high = highs.iloc[
            i - span:i + span + 1
        ]

        window_low = lows.iloc[
            i - span:i + span + 1
        ]

        if highs.iloc[i] >= window_high.max():
            pivot_highs.append(
                float(highs.iloc[i])
            )

        if lows.iloc[i] <= window_low.min():
            pivot_lows.append(
                float(lows.iloc[i])
            )

    return pivot_highs, pivot_lows


def zones(
    prices,
    current,
    support=True,
    tolerance=0.015,
):
    """Cluster nearby pivot prices into zones."""

    candidates = [
        p for p in prices
        if p > 0
        and (
            p <= current
            if support
            else p >= current
        )
    ]

    clusters = []

    for price in sorted(candidates):
        match = next(
            (
                cluster
                for cluster in clusters
                if abs(
                    price - cluster["level"]
                ) / cluster["level"] <= tolerance
            ),
            None,
        )

        if match:
            count = match["touches"]

            match["level"] = (
                match["level"] * count + price
            ) / (count + 1)

            match["touches"] += 1

        else:
            clusters.append({
                "level": price,
                "touches": 1,
            })

    clusters.sort(
        key=lambda item: (
            -item["touches"],
            abs(item["level"] - current),
        )
    )

    return [
        {
            "price": round(item["level"], 2),
            "touches": item["touches"],
            "distance_pct": round(
                (
                    item["level"] / current - 1
                ) * 100,
                2,
            ),
        }
        for item in clusters[:3]
    ]


def analyze_frame(x, name):
    """Analyze one timeframe."""

    if len(x) < 12:
        return {
            "timeframe": name,
            "status": "insufficient_history",
            "bars": len(x),
        }

    close = x["Close"]

    ema_fast = close.ewm(
        span=10,
        adjust=False,
    ).mean()

    ema_slow = close.ewm(
        span=20,
        adjust=False,
    ).mean()

    last = float(close.iloc[-1])

    if (
        last > ema_fast.iloc[-1]
        > ema_slow.iloc[-1]
    ):
        trend = "صاعد"

    elif (
        last < ema_fast.iloc[-1]
        < ema_slow.iloc[-1]
    ):
        trend = "هابط"

    else:
        trend = "عرضي"

    highs, lows = pivots(
        x.tail(120)
    )

    supports = zones(
        lows,
        last,
        support=True,
    )

    resistances = zones(
        highs,
        last,
        support=False,
    )

    volume_average = (
        x["Volume"]
        .shift(1)
        .rolling(
            10,
            min_periods=5,
        )
        .mean()
        .iloc[-1]
    )

    if (
        pd.notna(volume_average)
        and volume_average > 0
    ):
        relative_volume = float(
            x["Volume"].iloc[-1]
            / volume_average
        )
    else:
        relative_volume = None

    return {
        "timeframe": name,
        "status": "ok",
        "bars": len(x),
        "last_close": round(last, 2),
        "trend": trend,
        "support_zones": supports,
        "resistance_zones": resistances,
        "relative_volume": (
            round(relative_volume, 2)
            if relative_volume is not None
            else None
        ),
        "last_candle_bullish": bool(
            x["Close"].iloc[-1]
            > x["Open"].iloc[-1]
        ),
    }


def analyze(symbol):
    """Analyze three timeframes for one Saudi stock."""

    x = daily(symbol)

    if x is None or len(x) < 300:
        return {
            "symbol": str(symbol),
            "status": "insufficient_daily_data",
        }

    now = pd.Timestamp.now(
        tz="Asia/Riyadh"
    )

    last_date = pd.Timestamp(
        x.index[-1]
    ).date()

    # Avoid using today's incomplete daily candle.
    if (
        last_date == now.date()
        and now.hour < 15
    ):
        x = x.iloc[:-1]

    daily_result = analyze_frame(
        x,
        "daily",
    )

    weekly_result = analyze_frame(
        completed_bars(x, "weekly"),
        "weekly",
    )

    monthly_result = analyze_frame(
        completed_bars(x, "monthly"),
        "monthly",
    )

    complete = all(
        result.get("status") == "ok"
        for result in (
            daily_result,
            weekly_result,
            monthly_result,
        )
    )

    if not complete:
        classification = "بيانات غير كافية"

    elif (
        monthly_result["trend"] == "صاعد"
        and weekly_result["trend"] == "صاعد"
    ):
        classification = "مراقبة استثمار"

    elif (
        daily_result["trend"] == "صاعد"
        and weekly_result["trend"] != "هابط"
    ):
        classification = "مراقبة مضاربة"

    else:
        classification = "انتظار"

    return {
        "symbol": str(symbol),
        "status": (
            "ok" if complete else "partial"
        ),
        "classification": classification,
        "daily": daily_result,
        "weekly": weekly_result,
        "monthly": monthly_result,
        "note": (
            "Research only. No validated entry, "
            "profit target, or success probability."
        ),
    }


def main():
    symbols = sys.argv[1:] or [
        "1120",
        "2010",
        "7010",
    ]

    results = {}

    for symbol in symbols:
        try:
            results[symbol] = analyze(
                symbol
            )

            print(
                symbol,
                results[symbol].get(
                    "classification",
                    results[symbol]["status"],
                ),
            )

        except Exception as exc:
            results[symbol] = {
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

            print(
                symbol,
                "FAILED",
                exc,
            )

    output = Path(
        "multi_timeframe_results.json"
    )

    output.write_text(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(
        "Saved multi_timeframe_results.json"
    )


if __name__ == "__main__":
    main()
