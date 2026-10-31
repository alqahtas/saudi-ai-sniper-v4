
"""Saudi AI Sniper V6.1 — Strategy Research Engine.

Research and alerts only. No automatic orders.
Target: +2% | Stop: -1%.
Historical performance does not guarantee future returns.
"""

import logging
import os

import numpy as np
import pandas as pd
import yfinance as yf

LOG = logging.getLogger(__name__)

CAPITAL_SAR = 50000
MAX_ENTRY_SAR = 10000
RISK_PER_TRADE_SAR = 150
MIN_AVG_VALUE_SAR = 20000000

STOP_PCT = 0.01
TARGET_PCT = 0.02
TARGET_R = 2.0
MAX_HOLD_DAYS = 12
ROUND_TRIP_COST = 0.003

MIN_TRAIN_TRADES = 5
MIN_TEST_TRADES = 5
MIN_TEST_PROFIT_FACTOR = 1.0
MIN_TEST_AVG_RETURN = 0.0
MAX_TEST_DRAWDOWN = -20.0

WATCHLIST = (
    "2222 1120 1180 1150 1010 1050 "
    "2010 2020 1211 7010 7020 7030 "
    "2082 2083 5110 4001 4002 4003 "
    "4004 4005 4006 4013 4164 4200 "
    "4300 7200 7202 7203 7204 8010"
).split()


def daily(ticker):
    symbol = str(ticker).strip().upper()
    if not symbol.endswith(".SR"):
        symbol += ".SR"

    data = yf.download(
        symbol,
        period="5y",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
        timeout=18,
        multi_level_index=False,
    )
    return clean(data)


def clean(data):
    if data is None or len(data) == 0:
        return None

    x = data.copy()

    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)

    x.columns = [
        str(c).strip().title() for c in x.columns
    ]

    required = ["Open", "High", "Low", "Close", "Volume"]

    if any(c not in x.columns for c in required):
        return None

    x = x[required].copy()

    for column in required:
        x[column] = pd.to_numeric(
            x[column], errors="coerce"
        )

    x = x.replace([np.inf, -np.inf], np.nan)
    x = x.dropna(subset=required)
    x = x[
        (x["Open"] > 0)
        & (x["High"] > 0)
        & (x["Low"] > 0)
        & (x["Close"] > 0)
        & (x["Volume"] >= 0)
    ]

    x = x.sort_index()
    x = x[~x.index.duplicated(keep="last")]

    if len(x) < 300:
        return None

    return x


def indicators(data):
    x = clean(data)

    if x is None:
        raise ValueError("Insufficient OHLCV history")

    close = x["Close"]
    high = x["High"]
    low = x["Low"]
    volume = x["Volume"]

    x["ema20"] = close.ewm(
        span=20, adjust=False
    ).mean()
    x["ema50"] = close.ewm(
        span=50, adjust=False
    ).mean()
    x["ema100"] = close.ewm(
        span=100, adjust=False
    ).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(
        alpha=1 / 14, adjust=False
    ).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)
    x["rsi"] = 100 - 100 / (1 + rs)
    x["rsi"] = x["rsi"].fillna(50)

    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["atr"] = true_range.ewm(
        alpha=1 / 14, adjust=False
    ).mean()

    x["support"] = low.shift(1).rolling(
        30, min_periods=30
    ).min()

    x["resistance"] = high.shift(1).rolling(
        30, min_periods=30
    ).max()

    average_volume = volume.shift(1).rolling(
        20, min_periods=20
    ).mean()

    x["relvol"] = volume / average_volume.replace(
        0, np.nan
    )

    x["avgvalue"] = (
        close * volume
    ).shift(1).rolling(
        20, min_periods=20
    ).mean()

    candle_range = (
        high - low
    ).replace(0, np.nan)

    x["lowerwick"] = (
        np.minimum(x["Open"], close) - low
    ) / candle_range

    x["closepos"] = (
        close - low
    ) / candle_range

    x["macd"] = (
        close.ewm(span=12, adjust=False).mean()
        - close.ewm(span=26, adjust=False).mean()
    )

    x["macd_signal"] = x["macd"].ewm(
        span=9, adjust=False
    ).mean()

    return x


def signals(x):
    liquid = x["avgvalue"] >= MIN_AVG_VALUE_SAR

    uptrend = (
        (x["Close"] > x["ema50"])
        & (x["ema20"] > x["ema50"])
        & (x["ema50"] > x["ema100"])
    )

    bullish = x["Close"] > x["Open"]

    support_rebound = (
        liquid
        & bullish
        & (x["Low"] <= x["support"] * 1.025)
        & (x["Close"] >= x["support"])
        & x["rsi"].between(35, 65)
        & (x["lowerwick"] >= 0.25)
        & (x["closepos"] >= 0.60)
    )

    resistance_breakout = (
        liquid
        & uptrend
        & (x["Close"] > x["resistance"])
        & (x["relvol"] >= 1.30)
        & x["rsi"].between(50, 78)
        & bullish
    )

    trend_momentum = (
        liquid
        & uptrend
        & (x["Close"] > x["ema20"])
        & (x["Close"].shift(1) <= x["ema20"].shift(1))
        & (x["relvol"] >= 1.10)
        & x["rsi"].between(45, 70)
        & (x["macd"] > x["macd_signal"])
    )

    votes = (
        support_rebound.astype(int)
        + resistance_breakout.astype(int)
        + trend_momentum.astype(int)
    )

    ensemble = liquid & (votes >= 2)

    return {
        "support_rebound": support_rebound.fillna(False),
        "resistance_breakout": resistance_breakout.fillna(False),
        "trend_momentum": trend_momentum.fillna(False),
        "ensemble": ensemble.fillna(False),
    }


def simulate(x, signal, start, end):
    """Backtest with next-session entry and conservative stops."""

    trades = []
    equity = 1.0
    equity_curve = [equity]

    n = len(x)
    start = max(1, int(start))
    end = min(int(end), n - 2)

    i = start

    while i <= end:
        if not bool(signal.iloc[i]):
            i += 1
            continue

        entry_day = i + 1

        if entry_day >= n:
            break

        entry = float(x["Open"].iloc[entry_day])

        if not np.isfinite(entry) or entry <= 0:
            i += 1
            continue

        stop = entry * (1 - STOP_PCT)
        target = entry * (1 + TARGET_PCT)

        final_day = min(
            entry_day + MAX_HOLD_DAYS - 1,
            n - 1,
        )

        exit_price = float(
            x["Close"].iloc[final_day]
        )
        exit_day = final_day
        exit_reason = "time_exit"

        for j in range(entry_day, final_day + 1):
            op = float(x["Open"].iloc[j])
            lo = float(x["Low"].iloc[j])
            hi = float(x["High"].iloc[j])

            if op <= stop:
                exit_price = op
                exit_day = j
                exit_reason = "stop_gap"
                break

            if op >= target:
                exit_price = target
                exit_day = j
                exit_reason = "target_gap"
                break

            # If stop and target both occur in the same
            # daily candle, assume stop happens first.
            if lo <= stop:
                exit_price = stop
                exit_day = j
                exit_reason = "stop"
                break

            if hi >= target:
                exit_price = target
                exit_day = j
                exit_reason = "target"
                break

        gross_return = (
            exit_price / entry - 1
        )

        net_return = (
            gross_return - ROUND_TRIP_COST
        )

        trades.append({
            "entry_index": entry_day,
            "exit_index": exit_day,
            "entry": entry,
            "exit": exit_price,
            "gross_return": gross_return,
            "net_return": net_return,
            "reason": exit_reason,
        })

        equity *= max(0.0, 1 + net_return)
        equity_curve.append(equity)

        # Do not open overlapping positions.
        i = exit_day + 1

    returns = np.array(
        [t["net_return"] for t in trades],
        dtype=float,
    )

    count = len(returns)

    if count == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "total_return": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "wins": 0,
            "losses": 0,
            "target_hits": 0,
            "stop_hits": 0,
        }

    wins = returns[returns > 0]
    losses = returns[returns <= 0]

    gross_profit = float(wins.sum())
    gross_loss = float(abs(losses.sum()))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else (99.0 if gross_profit > 0 else 0.0)
    )

    curve = np.asarray(equity_curve)
    peak = np.maximum.accumulate(curve)
    drawdown = curve / peak - 1

    return {
        "trades": count,
        "win_rate": round(
            100 * len(wins) / count, 2
        ),
        "avg_return": round(
            100 * float(returns.mean()), 3
        ),
        "total_return": round(
            100 * (equity - 1), 3
        ),
        "profit_factor": round(
            profit_factor, 3
        ),
        "max_drawdown": round(
            100 * float(drawdown.min()), 3
        ),
        "wins": len(wins),
        "losses": len(losses),
        "target_hits": sum(
            t["reason"].startswith("target")
            for t in trades
        ),
        "stop_hits": sum(
            t["reason"].startswith("stop")
            for t in trades
        ),
    }


def score_metrics(metrics):
    if metrics["trades"] < MIN_TRAIN_TRADES:
        return -999999.0

    if metrics["avg_return"] <= 0:
        return -999999.0

    return (
        metrics["avg_return"] * 10
        + metrics["win_rate"] * 0.10
        + min(metrics["profit_factor"], 5) * 3
        + metrics["max_drawdown"] * 0.15
    )


def passes_test(metrics):
    return bool(
        metrics["trades"] >= MIN_TEST_TRADES
        and metrics["avg_return"] > MIN_TEST_AVG_RETURN
        and metrics["profit_factor"]
        > MIN_TEST_PROFIT_FACTOR
        and metrics["max_drawdown"]
        > MAX_TEST_DRAWDOWN
    )


def strategy_research(x):
    """Rank on training data; validate out of sample.

    Returns:
        selected, approved, results, signal_map
    """

    signal_map = signals(x)
    n = len(x)

    split = int(n * 0.65)
    split = max(180, min(split, n - 80))

    results = {}

    for name, signal in signal_map.items():
        train = simulate(
            x, signal, 120, split - 2
        )

        test = simulate(
            x, signal, split, n - 2
        )

        results[name] = {
            "train": train,
            "test": test,
            "train_score": score_metrics(train),
            "passed_test": passes_test(test),
        }

    ranked = sorted(
        results,
        key=lambda name: results[name]["train_score"],
        reverse=True,
    )

    # Prefer a training-qualified strategy that also
    # passes the predeclared out-of-sample criteria.
    # Do not optimize scores on the test set.
    selected = None

    for name in ranked:
        result = results[name]

        if (
            result["train_score"] > -999999
            and result["passed_test"]
        ):
            selected = name
            break

    approved = selected is not None

    if selected is None:
        # Diagnostic only: the highest training score.
        selected = ranked[0] if ranked else None

    return selected, approved, results, signal_map


def calculate(data, ticker=None, *args, **kwargs):
    """Return scanner-compatible Arabic result fields."""

    x = indicators(data)

    selected, approved, results, signal_map = (
        strategy_research(x)
    )

    last = x.iloc[-1]

    close = float(last["Close"])
    support = float(last["support"])
    resistance = float(last["resistance"])
    avgvalue = float(last["avgvalue"])
    relvol = float(last["relvol"])
    rsi = float(last["rsi"])

    liquid = (
        np.isfinite(avgvalue)
        and avgvalue >= MIN_AVG_VALUE_SAR
    )

    active_strategies = [
        name
        for name, signal in signal_map.items()
        if bool(signal.iloc[-1])
    ]

    selected_active = (
        selected in active_strategies
    )

    # Entry is conditional, not a live exchange quote.
    entry = round(close, 2)
    stop = round(entry * (1 - STOP_PCT), 2)
    target = round(entry * (1 + TARGET_PCT), 2)

    risk_per_share = max(entry - stop, 0.01)

    shares_by_risk = int(
        RISK_PER_TRADE_SAR / risk_per_share
    )

    shares_by_budget = int(
        min(CAPITAL_SAR, MAX_ENTRY_SAR) / entry
    )

    shares = max(
        0,
        min(shares_by_risk, shares_by_budget),
    )

    valid = bool(
        approved
        and selected_active
        and liquid
        and shares >= 1
    )

    selected_test = (
        results[selected]["test"]
        if selected in results
        else {}
    )

    score = (
        10 * int(liquid)
        + 10 * int(approved)
        + 10 * int(selected_active)
        + 10 * int(relvol >= 1.1)
        + 10 * int(close > last["ema50"])
    )

    date_value = x.index[-1]

    if hasattr(date_value, "strftime"):
        candle_date = date_value.strftime("%Y-%m-%d")
    else:
        candle_date = str(date_value)[:10]

    if valid:
        reason = (
            "Validated historical strategy; "
            "current daily signal detected. "
            "Live confirmation is still required."
        )
    elif not approved:
        reason = (
            "No strategy passed the historical "
            "out-of-sample requirements."
        )
    elif not selected_active:
        reason = (
            "Validated strategy has no current signal."
        )
    elif not liquid:
        reason = "Liquidity below required threshold."
    else:
        reason = "Entry conditions not fully satisfied."

    result = {
        "السهم": str(ticker or ""),
        "الرمز": str(ticker or ""),
        "التاريخ": candle_date,
        "تاريخ_الشمعة": candle_date,
        "الحالة": (
            "فرصة مشروطة" if valid else "لا دخول"
        ),
        "الاستراتيجية": selected or "",
        "الاستراتيجية_الأفضل": selected or "",
        "الاستراتيجيات_المتوافقة":
            active_strategies,
        "اجتاز_الاختبار_الخارجي": bool(approved),
        "نتائج_الاختبار": results,
        "نسبة_نجاح_تاريخية":
            selected_test.get("win_rate", 0),
        "عدد_صفقات_الاختبار":
            selected_test.get("trades", 0),
        "سبب_الإشارة": reason,
        "السعر": round(close, 2),
        "آخر_إغلاق": round(close, 2),
        "الدخول": entry,
        "سعر_الدخول": entry,
        "وقف_الخسارة": stop,
        "الوقف": stop,
        "الهدف": target,
        "هدف_الربح": target,
        "نسبة_الهدف": 2.0,
        "نسبة_الوقف": 1.0,
        "الكمية": shares,
        "قيمة_الصفقة": round(shares * entry, 2),
        "المخاطرة_بالريال":
            round(shares * risk_per_share, 2),
        "السيولة": round(avgvalue, 2),
        "متوسط_قيمة_التداول": round(avgvalue, 2),
        "حجم_نسبي": round(relvol, 2),
        "RSI": round(rsi, 2),
        "الدعم": round(support, 2),
        "المقاومة": round(resistance, 2),
        "التقييم": score,
        "الدرجة": score,
        "score": score,
    }

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    for symbol in WATCHLIST[:3]:
        try:
            data = daily(symbol)

            if data is None:
                print(symbol, "NO DATA")
                continue

            result = calculate(data, symbol)

            print(
                symbol,
                result["الحالة"],
                result["الاستراتيجية_الأفضل"],
                result["عدد_صفقات_الاختبار"],
                result["نسبة_نجاح_تاريخية"],
            )

        except Exception as exc:
            LOG.exception(
                "Research failed for %s: %s",
                symbol,
                exc,
            )
