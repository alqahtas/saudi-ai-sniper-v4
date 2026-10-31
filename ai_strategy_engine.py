
"""Saudi AI Sniper V8 - Strategy Engine.

Research only. No orders and no Telegram alerts.
Works with the full TASI universe through market_scanner.
"""

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from sniper_core import daily, clean, indicators

TARGET_PCT = 0.02
STOP_PCT = 0.01
COST_PCT = 0.003
MAX_HOLD = 10

MIN_AVG_VALUE = 20_000_000
MIN_TRAIN_TRADES = 5
MIN_TEST_TRADES = 5
WARMUP_BARS = 120


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def round_safe(value, digits=2):
    value = finite(value)
    return round(value, digits) if value is not None else None


def complete_daily(frame):
    if frame is None or frame.empty:
        return frame

    now = pd.Timestamp.now(tz="Asia/Riyadh")
    last = pd.Timestamp(frame.index[-1])

    if last.tzinfo is not None:
        last = last.tz_localize(None)

    # Exclude an unfinished Saudi trading session.
    if last.date() == now.date() and now.hour < 15:
        return frame.iloc[:-1].copy()

    return frame


def previous_period_trend(frame, rule):
    """Previous completed week/month only."""

    close = frame["Close"]
    periods = close.index.to_period(rule)

    grouped = close.groupby(periods).last()

    fast = grouped.ewm(
        span=5, adjust=False
    ).mean()

    slow = grouped.ewm(
        span=10, adjust=False
    ).mean()

    trend = pd.Series(
        np.select(
            [
                (grouped > fast) & (fast > slow),
                (grouped < fast) & (fast < slow),
            ],
            [1, -1],
            default=0,
        ),
        index=grouped.index,
    )

    previous = trend.shift(1).fillna(0)
    aligned = previous.reindex(periods)

    return pd.Series(
        aligned.to_numpy(dtype=int),
        index=frame.index,
    )


def build_features(raw):
    frame = indicators(raw)

    frame["weekly_trend"] = previous_period_trend(
        frame, "W-THU"
    )

    frame["monthly_trend"] = previous_period_trend(
        frame, "M"
    )

    frame["daily_up"] = (
        (frame["Close"] > frame["ema20"])
        & (frame["ema20"] > frame["ema50"])
    )

    frame["liquid"] = (
        frame["avgvalue"] >= MIN_AVG_VALUE
    )

    frame["near_support"] = (
        (frame["support"] > 0)
        & (frame["Low"] <= frame["support"] * 1.025)
        & (frame["Close"] >= frame["support"])
    )

    frame["breakout"] = (
        frame["Close"] > frame["resistance"]
    )

    frame["bullish_candle"] = (
        frame["Close"] > frame["Open"]
    )

    frame["strong_volume"] = (
        frame["relvol"] >= 1.30
    )

    # Compute MACD locally to avoid depending on
    # whether sniper_core exposes these columns.
    ema12 = frame["Close"].ewm(
        span=12, adjust=False
    ).mean()

    ema26 = frame["Close"].ewm(
        span=26, adjust=False
    ).mean()

    frame["macd"] = ema12 - ema26

    frame["macd_signal"] = frame["macd"].ewm(
        span=9, adjust=False
    ).mean()

    frame["positive_macd"] = (
        frame["macd"] > frame["macd_signal"]
    )

    return frame


def candidate_signals(frame):
    liquid = frame["liquid"]
    bullish = frame["bullish_candle"]
    weekly_ok = frame["weekly_trend"] >= 0

    breakout = (
        liquid
        & frame["breakout"]
        & frame["strong_volume"]
        & bullish
        & frame["rsi"].between(48, 78)
        & weekly_ok
    )

    support_rebound = (
        liquid
        & frame["near_support"]
        & bullish
        & (frame["closepos"] >= 0.65)
        & frame["rsi"].between(30, 65)
        & weekly_ok
    )

    trend_pullback = (
        liquid
        & frame["daily_up"]
        & (frame["Low"] <= frame["ema20"] * 1.015)
        & (frame["Close"] > frame["ema20"])
        & bullish
        & frame["positive_macd"]
        & weekly_ok
    )

    momentum = (
        liquid
        & frame["daily_up"]
        & frame["strong_volume"]
        & frame["positive_macd"]
        & frame["rsi"].between(50, 75)
        & (frame["weekly_trend"] == 1)
    )

    multi_timeframe = (
        liquid
        & (frame["weekly_trend"] == 1)
        & (frame["monthly_trend"] >= 0)
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
        "multi_timeframe": multi_timeframe.fillna(False),
    }


def empty_stats():
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


def backtest(frame, signal, start, end):
    """Signal at close, entry at next session open.

    No overlapping positions. Stop takes priority
    when both stop and target touch the same candle.
    Only fully observable trades are counted.
    """

    if frame is None or len(frame) < 2:
        return empty_stats()

    start = max(WARMUP_BARS, int(start))
    end = min(int(end), len(frame) - 1)

    if start >= end:
        return empty_stats()

    signal = pd.Series(
        signal,
        index=frame.index,
    ).fillna(False).astype(bool)

    returns = []
    reasons = []
    equity = 1.0
    curve = [equity]

    i = start

    while i < end:
        if not signal.iloc[i]:
            i += 1
            continue

        entry_day = i + 1
        last_day = entry_day + MAX_HOLD - 1

        # Do not force a trade to close at a
        # training/testing boundary.
        if last_day > end:
            i += 1
            continue

        entry = float(
            frame["Open"].iloc[entry_day]
        )

        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        stop = entry * (1 - STOP_PCT)
        target = entry * (1 + TARGET_PCT)

        exit_price = float(
            frame["Close"].iloc[last_day]
        )
        exit_day = last_day
        reason = "time"

        for j in range(entry_day, last_day + 1):
            op = float(frame["Open"].iloc[j])
            low = float(frame["Low"].iloc[j])
            high = float(frame["High"].iloc[j])

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

        equity *= max(0.0, 1.0 + net)
        curve.append(equity)

        i = exit_day + 1

    if not returns:
        return empty_stats()

    arr = np.asarray(returns, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]

    profit = float(wins.sum())
    loss = float(abs(losses.sum()))

    if loss > 0:
        pf = profit / loss
    elif profit > 0:
        pf = 99.0
    else:
        pf = 0.0

    curve = np.asarray(curve)
    peaks = np.maximum.accumulate(curve)
    drawdown = curve / peaks - 1

    return {
        "trades": len(arr),
        "win_rate_pct": round(
            100 * float((arr > 0).mean()), 2
        ),
        "avg_net_pct": round(
            100 * float(arr.mean()), 3
        ),
        "total_net_pct": round(
            100 * (equity - 1), 3
        ),
        "profit_factor": round(pf, 3),
        "max_drawdown_pct": round(
            100 * float(drawdown.min()), 3
        ),
        "target_hits": reasons.count("target"),
        "stop_hits": sum(
            reason in ("stop", "gap_stop")
            for reason in reasons
        ),
    }


def train_eligible(stats):
    return bool(
        stats
        and stats["trades"] >= MIN_TRAIN_TRADES
        and stats["avg_net_pct"] is not None
        and stats["avg_net_pct"] > 0
        and stats["profit_factor"] > 1
    )


def test_eligible(stats):
    return bool(
        stats
        and stats["trades"] >= MIN_TEST_TRADES
        and stats["avg_net_pct"] is not None
        and stats["avg_net_pct"] > 0
        and stats["profit_factor"] > 1
        and stats["max_drawdown_pct"] > -20
    )


def strategy_rank(stats):
    if not train_eligible(stats):
        return float("-inf")

    return (
        stats["avg_net_pct"]
        + min(stats["profit_factor"], 5) * 0.10
        + stats["max_drawdown_pct"] * 0.01
    )


def research(frame):
    """Training selection, separate validation."""

    signals = candidate_signals(frame)
    split = int(len(frame) * 0.65)

    results = {}

    for name, signal in signals.items():
        train = backtest(
            frame, signal, WARMUP_BARS, split - 1
        )
        test = backtest(
            frame, signal, split, len(frame) - 1
        )

        results[name] = {
            "train": train,
            "test": test,
            "train_eligible": train_eligible(train),
            "test_eligible": test_eligible(test),
            "active_signal": bool(signal.iloc[-1]),
        }

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


def analyze_stock(symbol):
    raw = daily(symbol)

    if raw is None:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
        }

    raw = complete_daily(clean(raw))

    if raw is None or len(raw) < 300:
        return {
            "symbol": symbol,
            "status": "insufficient_history",
        }

    frame = build_features(raw)
    selected, approved, results = research(frame)
    last = frame.iloc[-1]

    active = bool(
        selected
        and results[selected]["active_signal"]
    )

    trade_signal = bool(approved and active)

    return {
        "symbol": symbol,
        "status": "ok",
        "as_of_daily": str(frame.index[-1])[:10],
        "classification": (
            "مضاربة - إشارة تاريخية مشروطة"
            if trade_signal else "انتظار"
        ),
        "technical": {
            "last_close": round_safe(last["Close"]),
            "rsi": round_safe(last["rsi"]),
            "relative_volume": round_safe(last["relvol"]),
            "average_traded_value_sar": round_safe(
                last["avgvalue"]
            ),
            "support_30d": round_safe(last["support"]),
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
            "reference_close": round_safe(
                last["Close"]
            ),
            "target_pct": TARGET_PCT * 100,
            "stop_pct": STOP_PCT * 100,
            "test_trades": (
                results[selected]["test"]["trades"]
                if selected else 0
            ),
            "test_win_rate_pct": (
                results[selected]["test"]["win_rate_pct"]
                if selected else None
            ),
            "note": (
                "Research only. Live confirmation "
                "required before any entry."
            ),
        },
        "strategy_comparison": results,
    }


def main():
    # Explicit symbols for isolated tests.
    # Without arguments, use the full market directory.
    if len(sys.argv) > 1:
        symbols = sys.argv[1:]
    else:
        from market_scanner import (
            sahmk_session,
            tasi_equities,
        )

        session = sahmk_session()
        universe = tasi_equities(session)

        symbols = []
        seen = set()

        for item in universe:
            if isinstance(item, str):
                symbol = item
            elif isinstance(item, dict):
                symbol = (
                    item.get("symbol")
                    or item.get("ticker")
                    or item.get("code")
                    or item.get("stock_code")
                )
            else:
                continue

            symbol = str(symbol).replace(
                ".SR", ""
            ).strip()

            if symbol.isdigit() and symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)

    if not symbols:
        raise RuntimeError(
            "No TASI equities returned"
        )

    print(
        "Saudi AI Sniper V8",
        "Universe:", len(symbols),
        flush=True,
    )

    report = {
        "engine": "Saudi AI Sniper V8",
        "generated_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "universe_size": len(symbols),
        "settings": {
            "target_pct": TARGET_PCT * 100,
            "stop_pct": STOP_PCT * 100,
            "round_trip_cost_pct": COST_PCT * 100,
            "min_avg_value_sar": MIN_AVG_VALUE,
        },
        "stocks": {},
    }

    for number, symbol in enumerate(symbols, 1):
        print(
            f"[{number}/{len(symbols)}] {symbol}",
            flush=True,
        )

        try:
            result = analyze_stock(symbol)
        except Exception as exc:
            result = {
                "symbol": symbol,
                "status": "error",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

        report["stocks"][symbol] = result

        print(
            result.get(
                "classification",
                result["status"],
            ),
            flush=True,
        )

    path = Path("ai_strategy_results.json")

    path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    successful = sum(
        stock["status"] == "ok"
        for stock in report["stocks"].values()
    )

    print(
        f"Analyzed: {successful}/{len(symbols)}",
        flush=True,
    )
    print("Saved:", path)

    if successful == 0:
        raise RuntimeError(
            "No stocks successfully analyzed"
        )


if __name__ == "__main__":
    main()
