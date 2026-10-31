
"""Saudi AI Sniper V8 - SAHMK Historical Research Engine.

Read-only analysis. No automatic trading.
Historical results are not guaranteed future returns.
"""

import logging
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

LOG = logging.getLogger(__name__)

MIN_AVG_VALUE_SAR = 20_000_000
STOP_PCT = 0.01
TARGET_PCT = 0.02
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


def _historical_rows(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in (
        "data", "results", "candles",
        "history", "historical", "items"
    ):
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            rows = _historical_rows(value)
            if rows:
                return rows

    return []


def daily(ticker):
    """Retrieve up to three years of SAHMK daily candles."""

    symbol = str(ticker).strip().upper()
    symbol = symbol.replace(".SR", "")

    if symbol in ("^TASI", "TASI"):
        symbol = "TASI"
    elif not symbol.isdigit():
        raise ValueError("Invalid symbol: " + symbol)

    key = os.getenv("SAHMK_API_KEY", "").strip()

    if not key:
        raise RuntimeError("SAHMK_API_KEY is missing")

    end = date.today()
    start = end - timedelta(days=1100)

    url = (
        "https://api.sahmk.sa/api/v1/"
        f"historical/{symbol}/"
    )

    rows = []
    offset = 0
    limit = 500

    session = requests.Session()

    for page in range(10):
        response = session.get(
            url,
            headers={"X-API-Key": key},
            params={
                "interval": "1d",
                "from": start.isoformat(),
                "to": end.isoformat(),
                "limit": limit,
                "offset": offset,
            },
            timeout=40,
        )

        response.raise_for_status()
        payload = response.json()

        batch = _historical_rows(payload)

        if not batch:
            break

        rows.extend(batch)

        if not isinstance(payload, dict):
            break

        if not payload.get("has_more", False):
            break

        new_offset = offset + len(batch)

        if new_offset <= offset:
            break

        offset = new_offset

    if not rows:
        LOG.warning(
            "No historical candles for %s", symbol
        )
        return None

    result = clean(pd.DataFrame(rows))

    if result is None:
        LOG.warning(
            "%s: insufficient or invalid candles (%s)",
            symbol, len(rows)
        )

    return result


def clean(data):
    if data is None or isinstance(data, (str, bytes)):
        return None

    if isinstance(data, (dict, list)):
        data = pd.DataFrame(data)

    if not isinstance(data, pd.DataFrame):
        return None

    if data.empty:
        return None

    x = data.copy()

    if isinstance(x.columns, pd.MultiIndex):
        x.columns = x.columns.get_level_values(0)

    aliases = {
        "date": "Date",
        "datetime": "Date",
        "timestamp": "Date",
        "time": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "turnover": "Turnover",
        "value": "Turnover",
    }

    x.columns = [
        aliases.get(str(c).lower(), str(c))
        for c in x.columns
    ]

    if not isinstance(x.index, pd.DatetimeIndex):
        if "Date" not in x.columns:
            return None

        dates = pd.to_datetime(
            x["Date"],
            errors="coerce",
            utc=True
        )

        x.index = pd.DatetimeIndex(
            dates
        ).tz_localize(None)

    elif x.index.tz is not None:
        x.index = x.index.tz_localize(None)

    required = [
        "Open", "High", "Low",
        "Close", "Volume"
    ]

    if any(c not in x.columns for c in required):
        return None

    columns = required.copy()

    if "Turnover" in x.columns:
        columns.append("Turnover")

    x = x[columns].copy()

    for column in columns:
        x[column] = pd.to_numeric(
            x[column],
            errors="coerce"
        )

    x = x.replace(
        [np.inf, -np.inf], np.nan
    )

    x = x.dropna(subset=required)

    x = x[
        (x["Open"] > 0)
        & (x["High"] > 0)
        & (x["Low"] > 0)
        & (x["Close"] > 0)
        & (x["Volume"] >= 0)
    ]

    x = x[x.index.notna()]
    x = x.sort_index()
    x = x[~x.index.duplicated(keep="last")]

    return x if len(x) >= 300 else None


def indicators(data):
    x = clean(data)

    if x is None:
        raise ValueError(
            "At least 300 valid daily candles required"
        )

    c = x["Close"]
    h = x["High"]
    l = x["Low"]
    v = x["Volume"]

    for period in (20, 50, 100):
        x[f"ema{period}"] = c.ewm(
            span=period,
            adjust=False
        ).mean()

    delta = c.diff()

    gain = delta.clip(lower=0).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    loss = (-delta.clip(upper=0)).ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = gain / loss.replace(0, np.nan)

    x["rsi"] = (
        100 - 100 / (1 + rs)
    ).fillna(50)

    previous = c.shift(1)

    tr = pd.concat(
        [
            h - l,
            (h - previous).abs(),
            (l - previous).abs()
        ],
        axis=1
    ).max(axis=1)

    x["atr"] = tr.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    x["support"] = l.shift(1).rolling(
        30
    ).min()

    x["resistance"] = h.shift(1).rolling(
        30
    ).max()

    avg_volume = v.shift(1).rolling(
        20
    ).mean()

    x["relvol"] = (
        v / avg_volume.replace(0, np.nan)
    )

    if "Turnover" in x.columns:
        value = x["Turnover"].where(
            x["Turnover"] > 0,
            c * v
        )
    else:
        value = c * v

    x["avgvalue"] = value.shift(1).rolling(
        20
    ).mean()

    candle_range = (h - l).replace(
        0, np.nan
    )

    x["lowerwick"] = (
        np.minimum(x["Open"], c) - l
    ) / candle_range

    x["closepos"] = (
        c - l
    ) / candle_range

    x["macd"] = (
        c.ewm(span=12, adjust=False).mean()
        - c.ewm(span=26, adjust=False).mean()
    )

    x["macd_signal"] = x["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    return x


def signals(x):
    liquid = (
        x["avgvalue"] >= MIN_AVG_VALUE_SAR
    )

    trend = (
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

    breakout = (
        liquid
        & trend
        & (x["Close"] > x["resistance"])
        & (x["relvol"] >= 1.30)
        & x["rsi"].between(50, 78)
        & bullish
    )

    momentum = (
        liquid
        & trend
        & (x["Close"] > x["ema20"])
        & (
            x["Close"].shift(1)
            <= x["ema20"].shift(1)
        )
        & (x["relvol"] >= 1.10)
        & x["rsi"].between(45, 70)
        & (x["macd"] > x["macd_signal"])
    )

    votes = (
        support_rebound.astype(int)
        + breakout.astype(int)
        + momentum.astype(int)
    )

    return {
        "support_rebound":
            support_rebound.fillna(False),
        "resistance_breakout":
            breakout.fillna(False),
        "trend_momentum":
            momentum.fillna(False),
        "ensemble":
            (liquid & (votes >= 2)).fillna(False),
    }


def simulate(x, signal, start, end):
    trades = []
    equity = 1.0
    curve = [equity]

    n = len(x)
    i = max(1, int(start))
    end = min(int(end), n - 2)

    while i <= end:
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
            entry_day + MAX_HOLD_DAYS - 1,
            n - 1
        )

        exit_day = last_day
        exit_price = float(
            x["Close"].iloc[last_day]
        )
        reason = "time_exit"

        for j in range(entry_day, last_day + 1):
            op = float(x["Open"].iloc[j])
            low = float(x["Low"].iloc[j])
            high = float(x["High"].iloc[j])

            if op <= stop:
                exit_price = op
                exit_day = j
                reason = "stop_gap"
                break

            if op >= target:
                exit_price = target
                exit_day = j
                reason = "target_gap"
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

        net_return = (
            exit_price / entry
            - 1
            - ROUND_TRIP_COST
        )

        trades.append({
            "entry_index": entry_day,
            "exit_index": exit_day,
            "entry": entry,
            "exit": exit_price,
            "gross_return":
                exit_price / entry - 1,
            "net_return": net_return,
            "reason": reason,
        })

        equity *= max(0, 1 + net_return)
        curve.append(equity)

        i = exit_day + 1

    returns = np.asarray(
        [t["net_return"] for t in trades],
        dtype=float
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

    profit = float(wins.sum())
    loss = float(abs(losses.sum()))

    pf = (
        profit / loss
        if loss > 0
        else (99.0 if profit > 0 else 0.0)
    )

    curve = np.asarray(curve)
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
        "profit_factor": round(pf, 3),
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
        key=lambda name:
            results[name]["train_score"],
        reverse=True
    )

    selected = None

    for name in ranked:
        item = results[name]

        if (
            item["train_score"] > -999999
            and item["passed_test"]
        ):
            selected = name
            break

    approved = selected is not None

    if selected is None:
        selected = ranked[0] if ranked else None

    return selected, approved, results, signal_map


def calculate(data, ticker=None, *args, **kwargs):
    """Compatibility with existing market scanner."""

    if isinstance(data, (str, int)) and isinstance(
        ticker, (pd.DataFrame, list, dict)
    ):
        data, ticker = ticker, str(data)

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

    liquid = bool(
        np.isfinite(avgvalue)
        and avgvalue >= MIN_AVG_VALUE_SAR
    )

    active = [
        name
        for name, signal in signal_map.items()
        if bool(signal.iloc[-1])
    ]

    selected_active = selected in active

    valid = bool(
        approved
        and selected_active
        and liquid
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
        + 10 * int(relvol >= 1.10)
        + 10 * int(close > last["ema50"])
    )

    entry = round(close, 2)

    if valid:
        reason = (
            "Historical validation passed; "
            "live confirmation required."
        )
    elif not approved:
        reason = (
            "No strategy passed out-of-sample tests."
        )
    elif not selected_active:
        reason = "No current strategy signal."
    elif not liquid:
        reason = "Insufficient trading liquidity."
    else:
        reason = "Conditions not fully confirmed."

    candle_date = x.index[-1].strftime(
        "%Y-%m-%d"
    )

    return {
        "السهم": str(ticker or ""),
        "الرمز": str(ticker or ""),
        "التاريخ": candle_date,
        "تاريخ_الشمعة": candle_date,
        "الحالة":
            "فرصة مشروطة" if valid else "لا دخول",
        "الاستراتيجية": selected or "",
        "الاستراتيجية_الأفضل": selected or "",
        "الاستراتيجيات_المتوافقة": active,
        "اجتاز_الاختبار_الخارجي": approved,
        "نتائج_الاختبار": results,
        "نسبة_نجاح_تاريخية":
            selected_test.get("win_rate", 0),
        "عدد_صفقات_الاختبار":
            selected_test.get("trades", 0),
        "سبب_الإشارة": reason,
        "السعر": entry,
        "آخر_إغلاق": entry,
        "الدخول": entry,
        "سعر_الدخول": entry,
        "وقف_الخسارة":
            round(entry * (1 - STOP_PCT), 2),
        "الوقف":
            round(entry * (1 - STOP_PCT), 2),
        "الهدف":
            round(entry * (1 + TARGET_PCT), 2),
        "هدف_الربح":
            round(entry * (1 + TARGET_PCT), 2),
        "نسبة_الهدف": 2.0,
        "نسبة_الوقف": 1.0,
        "الكمية": None,
        "قيمة_الصفقة": None,
        "المخاطرة_بالريال": None,
        "السيولة": round(avgvalue, 2),
        "متوسط_قيمة_التداول": round(avgvalue, 2),
        "حجم_نسبي": round(relvol, 2),
        "RSI": round(rsi, 2),
        "الدعم": round(support, 2),
        "المقاومة": round(resistance, 2),
        "درجة_الجودة": score,
        "قيمة_التداول_اليومية_المتوسطة_مليون":
            round(avgvalue / 1_000_000, 3),
        "التقييم": score,
        "الدرجة": score,
        "score": score,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    symbol = "1120"

    data = daily(symbol)

    if data is None:
        raise RuntimeError(
            "No sufficient historical data for " + symbol
        )

    result = calculate(data, symbol)

    print("SYMBOL:", symbol)
    print("CANDLES:", len(data))
    print("FIRST:", data.index[0])
    print("LAST:", data.index[-1])
    print("LIQUIDITY:", result["السيولة"])
    print("SCORE:", result["درجة_الجودة"])
    print("STATUS:", result["الحالة"])
