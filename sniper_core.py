
"""Saudi AI Sniper V6 — adaptive strategy research engine.

Read-only. Historical backtests are not forecasts or trade guarantees.
Uses daily Yahoo Finance candles, not exchange-grade live data.
"""
import logging
import os

import numpy as np
import pandas as pd

LOG = logging.getLogger(__name__)

CAPITAL_SAR = 50000
MAX_ENTRY_SAR = 10000
RISK_PER_TRADE_SAR = 150
MIN_AVG_VALUE_SAR = 20000000
STOP_PCT = 0.025
TARGET_R = 2.0
MAX_HOLD_DAYS = 12
ROUND_TRIP_COST = 0.003
MIN_TEST_TRADES = 5

WATCHLIST = (
    "2222 1120 1180 1150 1010 1050 2010 2020 "
    "1211 7010 7020 7030 2082 2083 5110 "
    "4001 4002 4003 4004 4005 4006 4013 "
    "4164 4200 4300 7200 7202 7203 7204 8010"
).split()


def daily(ticker):
    """Download up to five years for meaningful historical testing."""
    import yfinance as yf

    try:
        d = yf.download(
            str(ticker) + ".SR",
            period="5y",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
            timeout=18,
            multi_level_index=False,
        )
        if d is None or d.empty:
            return None
        return clean(d)
    except Exception as exc:
        LOG.warning("Download %s failed: %s", ticker, exc)
        return None


def clean(data):
    if data is None or len(data) < 300:
        return None

    d = data.copy()
    if isinstance(d.columns, pd.MultiIndex):
        return None

    rename = {
        str(c): str(c).strip().title()
        for c in d.columns
    }
    d = d.rename(columns=rename)

    cols = ["Open", "High", "Low", "Close", "Volume"]
    if not set(cols).issubset(d.columns):
        return None

    d = d[cols].apply(pd.to_numeric, errors="coerce")
    d = d.dropna().sort_index()
    d = d[~d.index.duplicated(keep="last")]

    d = d[
        (d["Open"] > 0)
        & (d["High"] > 0)
        & (d["Low"] > 0)
        & (d["Close"] > 0)
        & (d["Volume"] >= 0)
    ]

    return d if len(d) >= 300 else None


def indicators(d):
    x = d.copy()
    close = x["Close"]
    high = x["High"]
    low = x["Low"]
    volume = x["Volume"]

    x["ema20"] = close.ewm(span=20, adjust=False).mean()
    x["ema50"] = close.ewm(span=50, adjust=False).mean()
    x["ema100"] = close.ewm(span=100, adjust=False).mean()

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
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    x["atr"] = tr.rolling(14).mean()
    x["support"] = low.shift(1).rolling(30).min()
    x["resistance"] = high.shift(1).rolling(30).max()

    x["volavg"] = volume.shift(1).rolling(20).mean()
    x["relvol"] = volume / x["volavg"].replace(0, np.nan)

    x["avgvalue"] = (
        close * volume
    ).shift(1).rolling(20).mean()

    candle_range = (high - low).replace(0, np.nan)
    x["closepos"] = (close - low) / candle_range
    x["lowerwick"] = (
        np.minimum(x["Open"], close) - low
    ) / candle_range

    return x


def signals(x):
    """All signals use only current and earlier completed candles."""
    liquid = x["avgvalue"] >= MIN_AVG_VALUE_SAR
    uptrend = (
        (x["Close"] > x["ema50"])
        & (x["ema20"] > x["ema50"])
    )

    support_rebound = (
        liquid
        & (x["Low"] <= x["support"] * 1.015)
        & (x["Close"] > x["support"])
        & (x["Close"] > x["Open"])
        & (x["closepos"] >= 0.65)
        & (x["lowerwick"] >= 0.15)
        & (x["rsi"].between(35, 65))
    )

    resistance_breakout = (
        liquid
        & uptrend
        & (x["Close"] > x["resistance"])
        & (x["relvol"] >= 1.30)
        & (x["rsi"].between(50, 78))
    )

    trend_momentum = (
        liquid
        & uptrend
        & (x["ema50"] > x["ema100"])
        & (x["Close"] > x["ema20"])
        & (x["Close"].shift(1) <= x["ema20"].shift(1))
        & (x["rsi"].between(45, 70))
        & (x["relvol"] >= 1.10)
    )

    base = {
        "support_rebound": support_rebound.fillna(False),
        "resistance_breakout": resistance_breakout.fillna(False),
        "trend_momentum": trend_momentum.fillna(False),
    }

    # An ensemble requires two independent strategies agreeing.
    count = sum(s.astype(int) for s in base.values())
    base["ensemble"] = (count >= 2).fillna(False)

    return base


def simulate(x, signal, first, last):
    """Simulate entry at next day's open, never same-day signal close.

    If target and stop are touched in the same daily candle,
    assume the stop executed first (conservative).
    """
    trades = []
    i = max(first, 120)
    last = min(last, len(x) - 2)

    while i <= last:
        if not bool(signal.iloc[i]):
            i += 1
            continue

        entry_i = i + 1
        entry = float(x["Open"].iloc[entry_i])
        atr = float(x["atr"].iloc[i])

        if not np.isfinite(atr) or atr <= 0 or entry <= 0:
            i += 1
            continue

        risk = max(entry * STOP_PCT, atr * 1.2)
        stop = entry - risk
        target = entry + TARGET_R * risk

        if stop <= 0:
            i += 1
            continue

        end = min(entry_i + MAX_HOLD_DAYS - 1, last + 1)
        exit_price = float(x["Close"].iloc[end])
        exit_i = end
        reason = "time"

        for j in range(entry_i, end + 1):
            op = float(x["Open"].iloc[j])
            lo = float(x["Low"].iloc[j])
            hi = float(x["High"].iloc[j])

            if op <= stop:
                exit_price = op
                exit_i = j
                reason = "stop_gap"
                break

            if op >= target:
                exit_price = target
                exit_i = j
                reason = "target"
                break

            if lo <= stop:
                exit_price = stop
                exit_i = j
                reason = "stop"
                break

            if hi >= target:
                exit_price = target
                exit_i = j
                reason = "target"
                break

        net_return = (
            exit_price / entry - 1 - ROUND_TRIP_COST
        )

        trades.append({
            "return": float(net_return),
            "reason": reason,
            "entry_index": entry_i,
            "exit_index": exit_i,
        })

        # Do not overlap trades within the same strategy.
        i = exit_i + 1

    return trades


def metrics(trades):
    if not trades:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "avg_return": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "profit_factor": 0.0,
        }

    returns = np.array(
        [t["return"] for t in trades],
        dtype=float,
    )

    equity = np.cumprod(1 + returns)
    peaks = np.maximum.accumulate(
        np.r_[1.0, equity]
    )[1:]

    drawdowns = equity / peaks - 1

    wins = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())

    return {
        "trades": len(trades),
        "win_rate": round(
            100 * float(np.mean(returns > 0)), 1
        ),
        "avg_return": round(
            100 * float(returns.mean()), 3
        ),
        "total_return": round(
            100 * float(equity[-1] - 1), 2
        ),
        "max_drawdown": round(
            100 * float(drawdowns.min()), 2
        ),
        "profit_factor": round(
            float(wins / losses), 2
        ) if losses > 0 else (
            99.0 if wins > 0 else 0.0
        ),
    }


def score_metrics(m):
    """Rank strategies using return, drawdown and sample size."""
    if m["trades"] < MIN_TEST_TRADES:
        return -999.0

    return (
        m["avg_return"] * 8
        + min(m["profit_factor"], 4) * 2
        + m["win_rate"] * 0.04
        + m["max_drawdown"] * 0.15
    )


def strategy_research(x):
    sig = signals(x)
    n = len(x)

    # First 65% for development; final 35% untouched
    # until the strategy ranking is evaluated.
    split = int(n * 0.65)

    results = {}

    for name, s in sig.items():
        train = metrics(
            simulate(x, s, 120, split - 2)
        )
        test = metrics(
            simulate(x, s, split, n - 2)
        )

        results[name] = {
            "train": train,
            "test": test,
            "train_score": score_metrics(train),
            "test_score": score_metrics(test),
        }

    # Rank by training results only. The test set is used
    # to approve or reject, not to select the winner.
    ordered = sorted(
        results,
        key=lambda name: results[name]["train_score"],
        reverse=True,
    )

    selected = ordered[0]
    best = results[selected]

    approved = (
        best["train"]["trades"] >= MIN_TEST_TRADES
        and best["test"]["trades"] >= MIN_TEST_TRADES
        and best["train"]["avg_return"] > 0
        and best["test"]["avg_return"] > 0
        and best["test"]["profit_factor"] > 1
        and best["test"]["max_drawdown"] > -20
    )

    return selected, approved, results, sig


def calculate(ticker, data):
    d = clean(data)
    if d is None:
        return None

    x = indicators(d)
    selected, approved, results, sig = strategy_research(x)

    last = x.iloc[-1]
    price = float(last["Close"])
    support = float(last["support"])
    resistance = float(last["resistance"])
    atr = float(last["atr"])
    avgvalue = float(last["avgvalue"])

    if not all(
        np.isfinite(v) and v > 0
        for v in (
            price, support, resistance, atr, avgvalue
        )
    ):
        return None

    active = [
        name for name, signal in sig.items()
        if name != "ensemble" and bool(signal.iloc[-1])
    ]

    selected_active = bool(sig[selected].iloc[-1])
    liquid = avgvalue >= MIN_AVG_VALUE_SAR

    # Only a historically approved strategy with a
    # current completed-candle signal is eligible.
    valid = approved and selected_active and liquid

    # A reference level, NOT an executable live quote.
    entry = price
    risk = max(entry * STOP_PCT, atr * 1.2)
    stop = entry - risk
    target = entry + TARGET_R * risk

    if stop <= 0:
        return None

    shares = min(
        int(MAX_ENTRY_SAR // entry),
        int(RISK_PER_TRADE_SAR // risk),
    )

    if shares < 1:
        valid = False

    test = results[selected]["test"]

    # This is a descriptive quality score, not a
    # calibrated probability of winning.
    quality = 0
    if approved:
        quality += 40
    if selected_active:
        quality += 20
    if liquid:
        quality += 10
    if len(active) >= 2:
        quality += 10
    if test["win_rate"] >= 55:
        quality += 10
    if test["profit_factor"] >= 1.5:
        quality += 10

    reason = (
        "استراتيجية مختبرة على الماضي مع إشارة فنية حالية"
        if valid else
        "لا توجد إشارة معتمدة من الاختبار التاريخي"
    )

    return {
        "السهم": str(ticker),
        "التاريخ": str(d.index[-1].date()),
        "الإطار_الزمني": "daily",
        "الاستراتيجيات": selected,
        "الاستراتيجية_الأفضل": selected,
        "الاستراتيجيات_المتوافقة": ", ".join(active),
        "عدد_الاستراتيجيات_المتوافقة": len(active),
        "اجتاز_الاختبار_الخارجي": bool(approved),
        "نتائج_الاختبار": results,
        "الإغلاق_المرجعي": round(price, 3),
        "الدعم": round(support, 3),
        "المقاومة": round(resistance, 3),
        "بداية_نطاق_الشراء": round(entry, 3),
        "نهاية_نطاق_الشراء": round(entry, 3),
        "وقف_تقديري": round(stop, 3),
        "هدف_تقديري": round(target, 3),
        "هدف_ثان": round(entry + 3 * risk, 3),
        "قيمة_التداول_اليومية_المتوسطة_مليون":
            round(avgvalue / 1000000, 2),
        "الحجم_النسبي": round(
            float(last["relvol"]), 2
        ) if np.isfinite(last["relvol"]) else 0,
        "RSI14": round(float(last["rsi"]), 1),
        "EMA20": round(float(last["ema20"]), 3),
        "EMA50": round(float(last["ema50"]), 3),
        "نسبة_العائد_للمخاطرة": TARGET_R,
        "كمية_افتراضية": shares,
        "إجمالي_الشراء_ريال": round(
            shares * entry, 2
        ),
        "درجة_الجودة": quality,
        "نسبة_نجاح_تاريخية": test["win_rate"],
        "عدد_صفقات_الاختبار": test["trades"],
        "أكبر_تراجع_تاريخي": test["max_drawdown"],
        "الحالة": (
            "فرصة مشروطة" if valid else "لا دخول"
        ),
        "سبب_الإشارة": reason,
        "السبب_عند_الرفض": (
            "" if valid else reason
        ),
        "نوع_البيانات":
            "شموع يومية تاريخية، ليست تأكيدًا لحظيًا",
    }
