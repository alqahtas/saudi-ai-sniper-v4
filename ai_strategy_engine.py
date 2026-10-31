
"""Saudi AI Sniper V7 - Adaptive Strategy Engine.

Combines:
- Daily / weekly / monthly market structure
- Support, resistance, trend, momentum and volume
- Optional SAHMK fundamental research
- Out-of-sample backtesting for short-term trades

Research only. No orders or Telegram messages.
No guaranteed returns or invented success probabilities.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from sniper_core import daily, clean, indicators
from multi_timeframe_engine import analyze_frame, completed_bars

TARGET_PCT = 0.015
STOP_PCT = 0.01
COST_PCT = 0.003
MAX_HOLD = 10

MIN_TEST_TRADES = 10
MIN_TRAIN_TRADES = 10
MIN_AVG_VALUE = 20_000_000

SYMBOLS = ["1120", "2010", "7010"]


def finite(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def round_safe(value, digits=2):
    x = finite(value)
    return round(x, digits) if x is not None else None


def complete_daily(x):
    """Exclude today's unfinished daily candle."""

    if x is None or len(x) == 0:
        return x

    now = pd.Timestamp.now(tz="Asia/Riyadh")
    last = pd.Timestamp(x.index[-1])

    if last.tzinfo is not None:
        last = last.tz_localize(None)

    if last.date() == now.date() and now.hour < 15:
        return x.iloc[:-1]

    return x


def previous_period_trend(x, rule):
    """Use only completed prior periods in historical signals."""

    close = x["Close"]

    grouped = close.resample(rule).last()

    fast = grouped.ewm(
        span=5, adjust=False
    ).mean()

    slow = grouped.ewm(
        span=10, adjust=False
    ).mean()

    trend = pd.Series(
        np.where(
            (grouped > fast) & (fast > slow),
            1,
            np.where(
                (grouped < fast) & (fast < slow),
                -1,
                0,
            ),
        ),
        index=grouped.index,
    )

    # The previous completed period is known without
    # using the current week's/month's final close.
    trend = trend.shift(1)

    aligned = trend.reindex(
        x.index,
        method="ffill",
    )

    return aligned.fillna(0).astype(int)


def build_features(raw):
    x = indicators(raw)

    x["weekly_trend"] = previous_period_trend(
        x, "W-THU"
    )

    x["monthly_trend"] = previous_period_trend(
        x, "ME"
    )

    x["daily_up"] = (
        (x["Close"] > x["ema20"])
        & (x["ema20"] > x["ema50"])
    )

    x["daily_down"] = (
        (x["Close"] < x["ema20"])
        & (x["ema20"] < x["ema50"])
    )

    x["liquid"] = (
        x["avgvalue"] >= MIN_AVG_VALUE
    )

    x["near_support"] = (
        (x["support"] > 0)
        & (x["Low"] <= x["support"] * 1.025)
        & (x["Close"] >= x["support"])
    )

    x["breakout"] = (
        x["Close"] > x["resistance"]
    )

    x["bullish_candle"] = (
        x["Close"] > x["Open"]
    )

    x["strong_volume"] = (
        x["relvol"] >= 1.30
    )

    x["positive_macd"] = (
        x["macd"] > x["macd_signal"]
    )

    return x


def candidate_signals(x):
    """Different strategy combinations, not one fixed rule."""

    liquid = x["liquid"]
    bullish = x["bullish_candle"]
    weekly_ok = x["weekly_trend"] >= 0

    breakout = (
        liquid
        & x["breakout"]
        & x["strong_volume"]
        & bullish
        & x["rsi"].between(48, 78)
        & weekly_ok
    )

    support_rebound = (
        liquid
        & x["near_support"]
        & bullish
        & (x["closepos"] >= 0.65)
        & x["rsi"].between(30, 65)
        & weekly_ok
    )

    trend_pullback = (
        liquid
        & x["daily_up"]
        & (x["Low"] <= x["ema20"] * 1.015)
        & (x["Close"] > x["ema20"])
        & bullish
        & x["positive_macd"]
        & weekly_ok
    )

    momentum = (
        liquid
        & x["daily_up"]
        & x["strong_volume"]
        & x["positive_macd"]
        & x["rsi"].between(50, 75)
        & (x["weekly_trend"] == 1)
    )

    combined = (
        liquid
        & (x["weekly_trend"] == 1)
        & (x["monthly_trend"] >= 0)
        & bullish
        & (
            breakout
            | support_rebound
            | trend_pullback
            | momentum
        )
    )

    return {
        "breakout": breakout.fillna(False),
        "support_rebound": support_rebound.fillna(False),
        "trend_pullback": trend_pullback.fillna(False),
        "momentum": momentum.fillna(False),
        "multi_timeframe": combined.fillna(False),
    }


def backtest(x, signal, start, end):
    """Next-day entry, stop-first daily-candle assumption.

    Each evaluation is restricted to its own date segment.
    """

    returns = []
    reasons = []
    i = max(120, int(start))
    end = min(int(end), len(x) - 1)

    while i < end:
        if not bool(signal.iloc[i]):
            i += 1
            continue

        entry_day = i + 1
        entry = float(x["Open"].iloc[entry_day])

        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        stop = entry * (1 - STOP_PCT)
        target = entry * (1 + TARGET_PCT)

        last_day = min(
            entry_day + MAX_HOLD - 1,
            end,
        )

        exit_price = float(
            x["Close"].iloc[last_day]
        )

        exit_day = last_day
        reason = "time"

        for j in range(entry_day, last_day + 1):
            op = float(x["Open"].iloc[j])
            low = float(x["Low"].iloc[j])
            high = float(x["High"].iloc[j])

            if op <= stop:
                exit_price = op
                exit_day = j
                reason = "gap_stop"
                break

            if op >= target:
                exit_price = target
                exit_day = j
                reason = "target"
                break

            if low <= stop:
                exit_price = stop
                exit_day = j
                reason = "stop"
                break

            if high >= target:
                exit_price = target
                exit_day = j
                reason = "target"
                break

        net = exit_price / entry - 1 - COST_PCT

        returns.append(net)
        reasons.append(reason)

        i = exit_day + 1

    if not returns:
        return {
            "trades": 0,
            "win_rate_pct": None,
            "avg_net_pct": None,
            "total_net_pct": None,
            "profit_factor": None,
            "max_drawdown_pct": None,
            "target_hits": 0,
            "stop_hits": 0,
        }

    arr = np.asarray(returns)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]

    profit = float(wins.sum())
    loss = float(abs(losses.sum()))

    pf = (
        profit / loss
        if loss > 0
        else (99.0 if profit > 0 else 0.0)
    )

    curve = np.cumprod(
        np.r_[1.0, 1.0 + arr]
    )

    peaks = np.maximum.accumulate(curve)
    dd = curve / peaks - 1

    return {
        "trades": len(arr),
        "win_rate_pct": round(
            100 * float((arr > 0).mean()), 2
        ),
        "avg_net_pct": round(
            100 * float(arr.mean()), 3
        ),
        "total_net_pct": round(
            100 * float(curve[-1] - 1), 3
        ),
        "profit_factor": round(pf, 3),
        "max_drawdown_pct": round(
            100 * float(dd.min()), 3
        ),
        "target_hits": reasons.count("target"),
        "stop_hits": sum(
            r in ("stop", "gap_stop")
            for r in reasons
        ),
    }


def train_eligible(stats):
    return (
        stats["trades"] >= MIN_TRAIN_TRADES
        and stats["avg_net_pct"] is not None
        and stats["avg_net_pct"] > 0
        and stats["profit_factor"] > 1
    )


def test_eligible(stats):
    return (
        stats["trades"] >= MIN_TEST_TRADES
        and stats["avg_net_pct"] is not None
        and stats["avg_net_pct"] > 0
        and stats["profit_factor"] > 1
        and stats["max_drawdown_pct"] > -20
    )


def strategy_rank(stats):
    """Training-only ranking."""

    return (
        stats["avg_net_pct"]
        + min(stats["profit_factor"], 5) * 0.10
        + stats["max_drawdown_pct"] * 0.01
    )


def research(x):
    signal_map = candidate_signals(x)

    split = int(len(x) * 0.65)

    results = {}

    for name, signal in signal_map.items():
        train = backtest(
            x, signal, 120, split - 1
        )

        test = backtest(
            x, signal, split, len(x) - 1
        )

        results[name] = {
            "train": train,
            "test": test,
            "train_eligible": train_eligible(train),
            "test_eligible": test_eligible(test),
            "active_signal": bool(
                signal.iloc[-1]
            ),
        }

    # Select by training results only.
    eligible = [
        name for name, result in results.items()
        if result["train_eligible"]
    ]

    eligible.sort(
        key=lambda name: strategy_rank(
            results[name]["train"]
        ),
        reverse=True,
    )

    selected = eligible[0] if eligible else None

    approved = bool(
        selected
        and results[selected]["test_eligible"]
    )

    return selected, approved, results


def frame_report(x, label):
    try:
        return analyze_frame(x, label)
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
        }


def fundamental_report(symbol):
    """Optional research, not used as historical test data."""

    if not os.environ.get("SAHMK_API_KEY"):
        return {
            "status": "not_configured",
            "note": "SAHMK_API_KEY unavailable",
        }

    try:
        from fundamental_engine import analyze
        result = analyze(symbol)

        return {
            "status": "ok",
            "metrics": result.get("metrics", {}),
            "risk_flags": result.get(
                "risk_flags", []
            ),
            "report_dates": result.get(
                "report_dates", {}
            ),
            "data_errors": result.get(
                "data_errors", {}
            ),
        }

    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
        }


def analyze_stock(symbol):
    raw = complete_daily(
        clean(daily(symbol))
    )

    if raw is None or len(raw) < 300:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
        }

    x = build_features(raw)

    weekly = completed_bars(
        raw, "weekly"
    )

    monthly = completed_bars(
        raw, "monthly"
    )

    d = frame_report(raw, "daily")
    w = frame_report(weekly, "weekly")
    m = frame_report(monthly, "monthly")

    selected, approved, results = research(x)

    last = x.iloc[-1]

    investment_watch = (
        w.get("trend") == "صاعد"
        and m.get("trend") == "صاعد"
    )

    trade_signal = bool(
        approved
        and selected
        and results[selected]["active_signal"]
    )

    if trade_signal:
        classification = "مضاربة - إشارة تاريخية مشروطة"
    elif investment_watch:
        classification = "مراقبة استثمار"
    else:
        classification = "انتظار"

    # Daily-close reference, not a live executable quote.
    reference = float(last["Close"])

    selected_test = (
        results[selected]["test"]
        if selected
        else None
    )

    return {
        "symbol": symbol,
        "status": "ok",
        "as_of_daily": str(raw.index[-1])[:10],
        "classification": classification,
        "timeframes": {
            "daily": d,
            "weekly": w,
            "monthly": m,
        },
        "technical": {
            "last_close": round_safe(reference),
            "rsi": round_safe(last["rsi"]),
            "relative_volume": round_safe(
                last["relvol"]
            ),
            "support_30d": round_safe(
                last["support"]
            ),
            "resistance_30d": round_safe(
                last["resistance"]
            ),
            "weekly_trend_code": int(
                last["weekly_trend"]
            ),
            "monthly_trend_code": int(
                last["monthly_trend"]
            ),
        },
        "short_term": {
            "selected_strategy": selected,
            "approved_out_of_sample": approved,
            "current_daily_signal": trade_signal,
            "reference_close": round_safe(reference),
            "target_reference": round_safe(
                reference * (1 + TARGET_PCT)
            ),
            "stop_reference": round_safe(
                reference * (1 - STOP_PCT)
            ),
            "target_pct": TARGET_PCT * 100,
            "stop_pct": STOP_PCT * 100,
            "test_win_rate_pct": (
                selected_test["win_rate_pct"]
                if selected_test else None
            ),
            "test_trades": (
                selected_test["trades"]
                if selected_test else 0
            ),
            "note": (
                "Historical win rate is not a calibrated "
                "future probability. Entry requires "
                "live price and liquidity confirmation."
            ),
        },
        "investment": {
            "monthly_weekly_aligned": bool(
                investment_watch
            ),
            "status": (
                "Technical watch only; fundamental "
                "and risk review required"
            ),
        },
        "strategy_comparison": results,
        "fundamentals": fundamental_report(symbol),
    }


def main():
    symbols = sys.argv[1:] or SYMBOLS
    output = {
        "engine": "Saudi AI Sniper V7 Research",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "settings": {
            "target_pct": 1.5,
            "stop_pct": 1.0,
            "round_trip_cost_pct": 0.3,
            "max_hold_days": MAX_HOLD,
            "minimum_test_trades": MIN_TEST_TRADES,
        },
        "stocks": {},
    }

    for symbol in symbols:
        try:
            result = analyze_stock(symbol)
            output["stocks"][symbol] = result

            print(
                symbol,
                result.get("classification",
                           result["status"]),
            )

        except Exception as exc:
            output["stocks"][symbol] = {
                "status": "error",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }
            print(symbol, "ERROR:", exc)

    path = Path("ai_strategy_results.json")

    path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    print("Saved:", path)


if __name__ == "__main__":
    main()
