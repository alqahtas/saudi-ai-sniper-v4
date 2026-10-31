
"""Saudi AI Sniper V10 - Live Price Monitor.

Research and alerts only. No automatic orders.

Features:
- Scan the full Saudi stock market
- Identify support and rebound zones
- Identify conditional breakout entries
- Monitor prices when a quote provider is configured
- Track targets, stops and invalidations
- Prevent duplicate alerts

Dependencies:
    pandas, numpy, requests
    market_scanner.py
    sniper_core.py
    ai_strategy_engine.py
    v9_indicators.py
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from market_scanner import sahmk_session, tasi_equities
from sniper_core import daily, clean
from ai_strategy_engine import complete_daily
from v9_indicators import (
    advanced_features,
    candidate_signals,
)

WATCHLIST_FILE = Path("v10_watchlist.json")
STATE_FILE = Path("v10_monitor_state.json")
EVENTS_FILE = Path("v10_events.json")

MIN_AVG_VALUE = 20_000_000
MAX_DISTANCE_PCT = 4.0
MIN_RISK_REWARD = 1.5

DEFAULT_TARGET_PCT = 2.0
DEFAULT_STOP_PCT = 1.0

SCAN_INTERVAL_SECONDS = 60
REQUEST_TIMEOUT = 15

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
)
TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
)

SAHMK_API_KEY = os.getenv(
    "SAHMK_API_KEY", ""
)

# A documented quote endpoint must be configured.
# Do not guess the SAHMK API URL.
QUOTE_URL_TEMPLATE = os.getenv(
    "SAHMK_QUOTE_URL_TEMPLATE", ""
)


def now_utc():
    return datetime.now(
        timezone.utc
    ).isoformat()


def finite(value):
    try:
        number = float(value)
        return (
            number
            if np.isfinite(number)
            else None
        )
    except (TypeError, ValueError):
        return None


def load_json(path, default):
    if not path.exists():
        return default

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return default


def save_json(path, value):
    temporary = path.with_suffix(
        path.suffix + ".tmp"
    )

    temporary.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    temporary.replace(path)


def get_symbols():
    session = sahmk_session()
    items = tasi_equities(session)

    result = []
    seen = set()

    for item in items:
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

        if symbol is None:
            continue

        symbol = (
            str(symbol)
            .strip()
            .upper()
            .replace(".SR", "")
        )

        if (
            symbol.isdigit()
            and symbol not in seen
        ):
            seen.add(symbol)
            result.append(symbol)

    return result


def support_candidates(x):
    """Historical support levels only."""

    high = x["High"].astype(float)
    low = x["Low"].astype(float)
    close = x["Close"].astype(float)

    levels = {
        "low_20": finite(
            low.shift(1).tail(20).min()
        ),
        "low_60": finite(
            low.shift(1).tail(60).min()
        ),
        "ema_20": finite(
            x["ema20"].iloc[-1]
        ),
        "ema_50": finite(
            x["ema50"].iloc[-1]
        ),
        "bollinger_lower": finite(
            x["bb_lower"].iloc[-1]
        ),
        "donchian_low": finite(
            x["donchian_low20"].iloc[-1]
        ),
        "pivot_s1": finite(
            x["pivot_s1"].iloc[-1]
        ),
        "rolling_vwap": finite(
            x["rolling_vwap20"].iloc[-1]
        ),
    }

    price = finite(close.iloc[-1])

    if price is None or price <= 0:
        return []

    candidates = []

    for name, level in levels.items():
        if level is None or level <= 0:
            continue

        distance = (
            (price - level)
            / price * 100
        )

        if (
            -1.0 <= distance
            <= MAX_DISTANCE_PCT
        ):
            candidates.append({
                "source": name,
                "level": round(level, 4),
                "distance_pct": round(
                    distance, 3
                ),
            })

    return candidates


def build_zones(candidates, price):
    """Cluster nearby support indicators."""

    if not candidates:
        return []

    tolerance = max(
        price * 0.006,
        0.01,
    )

    ordered = sorted(
        candidates,
        key=lambda item: item["level"]
    )

    clusters = []

    for item in ordered:
        level = item["level"]

        if (
            clusters
            and level - clusters[-1][-1]["level"]
            <= tolerance
        ):
            clusters[-1].append(item)
        else:
            clusters.append([item])

    zones = []

    for cluster in clusters:
        values = [
            item["level"]
            for item in cluster
        ]

        bottom = min(values)
        top = max(values)

        zones.append({
            "bottom": round(bottom, 4),
            "top": round(top, 4),
            "midpoint": round(
                sum(values) / len(values), 4
            ),
            "confirmations": len(cluster),
            "sources": [
                item["source"]
                for item in cluster
            ],
            "distance_pct": round(
                (price - top)
                / price * 100,
                3,
            ),
        })

    zones.sort(
        key=lambda item: (
            -item["confirmations"],
            abs(item["distance_pct"]),
        )
    )

    return zones


def resistance_level(x):
    high = x["High"].astype(float)

    levels = [
        finite(
            high.shift(1).tail(20).max()
        ),
        finite(
            x["donchian_high20"].iloc[-1]
        ),
        finite(
            x["pivot_r1"].iloc[-1]
        ),
    ]

    levels = [
        level for level in levels
        if level is not None
        and level > 0
    ]

    return (
        round(min(levels), 4)
        if levels else None
    )


def create_plan(symbol):
    raw = daily(symbol)

    if raw is None:
        return None

    raw = complete_daily(
        clean(raw)
    )

    if (
        raw is None
        or len(raw) < 220
    ):
        return None

    x = advanced_features(raw)

    close = finite(
        x["Close"].iloc[-1]
    )

    if close is None or close <= 0:
        return None

    liquidity = finite(
        x["avg_value_v9"].iloc[-1]
    )

    if (
        liquidity is None
        or liquidity < MIN_AVG_VALUE
    ):
        return None

    candidates = support_candidates(x)
    zones = build_zones(
        candidates,
        close,
    )

    resistance = resistance_level(x)

    signals = candidate_signals(raw)

    active = [
        name
        for name, signal in signals.items()
        if bool(signal.iloc[-1])
    ]

    rsi = finite(
        x["rsi14"].iloc[-1]
    )

    adx = finite(
        x["adx"].iloc[-1]
    )

    relvol = finite(
        x["rel_volume_v9"].iloc[-1]
    )

    trend = (
        close > x["ema50"].iloc[-1]
        and x["ema20"].iloc[-1]
        > x["ema50"].iloc[-1]
    )

    return {
        "symbol": symbol,
        "as_of": str(
            x.index[-1]
        )[:10],
        "reference_close": close,
        "liquidity_sar": liquidity,
        "rsi": rsi,
        "adx": adx,
        "relative_volume": relvol,
        "uptrend": bool(trend),
        "support_zones": zones[:3],
        "resistance": resistance,
        "active_strategies": active,
        "status": "watching",
    }


def build_full_watchlist():
    symbols = get_symbols()

    print(
        "Scanning full TASI universe:",
        len(symbols),
        flush=True,
    )

    stocks = {}
    errors = {}

    for index, symbol in enumerate(
        symbols, 1
    ):
        print(
            f"[{index}/{len(symbols)}]",
            symbol,
            flush=True,
        )

        try:
            plan = create_plan(symbol)

            if plan is not None:
                stocks[symbol] = plan

        except Exception as exc:
            errors[symbol] = str(exc)

    report = {
        "engine": "Saudi AI Sniper V10",
        "generated_utc": now_utc(),
        "universe_size": len(symbols),
        "watched_stocks": len(stocks),
        "errors": errors,
        "stocks": stocks,
    }

    save_json(
        WATCHLIST_FILE,
        report,
    )

    print(
        "Watchlist saved:",
        len(stocks),
        "stocks",
        flush=True,
    )

    return report


def fetch_quote(symbol):
    """Fetch a quote only from a configured API.

    Required env:
      SAHMK_QUOTE_URL_TEMPLATE

    Example format:
      https://your-documented-provider/path/{symbol}

    The endpoint must be verified against
    the provider's actual API documentation.
    """

    if not QUOTE_URL_TEMPLATE:
        raise RuntimeError(
            "SAHMK_QUOTE_URL_TEMPLATE "
            "is not configured"
        )

    url = QUOTE_URL_TEMPLATE.format(
        symbol=symbol
    )

    headers = {
        "Accept": "application/json",
    }

    if SAHMK_API_KEY:
        headers["Authorization"] = (
            f"Bearer {SAHMK_API_KEY}"
        )

    response = requests.get(
        url,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    data = response.json()

    if not isinstance(data, dict):
        raise ValueError(
            "Quote response must be an object"
        )

    payload = data.get("data", data)

    if not isinstance(payload, dict):
        raise ValueError(
            "Invalid quote payload"
        )

    price = None

    for key in (
        "last",
        "last_price",
        "lastPrice",
        "price",
        "close",
    ):
        if key in payload:
            price = finite(payload[key])
            if price is not None:
                break

    if price is None or price <= 0:
        raise ValueError(
            f"No valid price for {symbol}"
        )

    timestamp = (
        payload.get("timestamp")
        or payload.get("time")
        or payload.get("updated_at")
    )

    if timestamp is None:
        raise ValueError(
            "Quote has no provider timestamp"
        )

    return {
        "symbol": symbol,
        "price": price,
        "provider_timestamp": str(
            timestamp
        ),
        "received_utc": now_utc(),
    }


def evaluate_price(plan, quote):
    """Evaluate support, rebound and breakout.

    These are watch events, not buy orders.
    """

    price = quote["price"]
    symbol = plan["symbol"]

    events = []

    for zone in plan.get(
        "support_zones", []
    ):
        bottom = zone["bottom"]
        top = zone["top"]

        # Price enters the support area.
        if bottom <= price <= top:
            events.append({
                "symbol": symbol,
                "type": "support_touch",
                "status": "watch",
                "price": price,
                "zone": zone,
                "message": (
                    "السعر داخل منطقة دعم محتملة. "
                    "ننتظر تأكيد الارتداد."
                ),
            })

        # Price breaks below support.
        elif price < bottom * 0.995:
            events.append({
                "symbol": symbol,
                "type": "support_break",
                "status": "invalidated",
                "price": price,
                "zone": zone,
                "message": (
                    "السعر كسر منطقة الدعم. "
                    "لا نعتمد الارتداد."
                ),
            })

    resistance = plan.get(
        "resistance"
    )

    if (
        resistance is not None
        and price > resistance
        and plan.get("uptrend")
        and plan.get("active_strategies")
    ):
        events.append({
            "symbol": symbol,
            "type": "breakout_watch",
            "status": "conditional",
            "price": price,
            "resistance": resistance,
            "message": (
                "اختراق سعري محتمل. "
                "يلزم تأكيد الحجم والسيولة "
                "قبل اعتبار الدخول مفعلًا."
            ),
        })

    return events


def send_telegram(message):
    if not (
        TELEGRAM_TOKEN
        and TELEGRAM_CHAT_ID
    ):
        print(
            "Telegram not configured:",
            message,
            flush=True,
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
        },
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            "Telegram rejected message"
        )

    return True


def event_key(event):
    zone = event.get("zone") or {}

    return "|".join([
        str(event["symbol"]),
        str(event["type"]),
        str(zone.get("bottom", "")),
        str(zone.get("top", "")),
        str(event.get("resistance", "")),
    ])


def monitor_once():
    watchlist = load_json(
        WATCHLIST_FILE, {}
    )

    stocks = watchlist.get(
        "stocks", {}
    )

    if not stocks:
        raise RuntimeError(
            "Watchlist empty. "
            "Run with --scan first."
        )

    state = load_json(
        STATE_FILE, {}
    )

    history = load_json(
        EVENTS_FILE, []
    )

    new_events = []
    failures = {}

    for symbol, plan in stocks.items():
        try:
            quote = fetch_quote(symbol)

            events = evaluate_price(
                plan,
                quote,
            )

            current_keys = {
                event_key(event)
                for event in events
            }

            previous_keys = set(
                state.get(symbol, [])
            )

            for event in events:
                key = event_key(event)

                if key in previous_keys:
                    continue

                event["detected_utc"] = (
                    now_utc()
                )

                event["quote"] = quote

                new_events.append(
                    event
                )

                message = (
                    "Saudi AI Sniper V10\n"
                    f"السهم: {symbol}\n"
                    f"السعر: {quote['price']}\n"
                    f"الحالة: {event['status']}\n"
                    f"{event['message']}\n"
                    "تنبيه مراقبة وليس أمر شراء."
                )

                try:
                    send_telegram(message)
                except Exception as exc:
                    print(
                        "Telegram error:",
                        exc,
                        flush=True,
                    )

            state[symbol] = sorted(
                current_keys
            )

        except Exception as exc:
            failures[symbol] = str(exc)

    history.extend(new_events)

    save_json(
        STATE_FILE,
        state,
    )

    save_json(
        EVENTS_FILE,
        history[-5000:],
    )

    print(
        "New events:",
        len(new_events),
        "Quote errors:",
        len(failures),
        flush=True,
    )

    if failures:
        print(
            json.dumps(
                failures,
                ensure_ascii=False,
            ),
            flush=True,
        )

    return new_events


def main():
    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan all TASI stocks",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Monitor one quote cycle",
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuous monitoring",
    )

    args = parser.parse_args()

    if args.scan:
        build_full_watchlist()

    if args.once:
        monitor_once()

    if args.loop:
        if not QUOTE_URL_TEMPLATE:
            raise RuntimeError(
                "Configure verified SAHMK "
                "quote endpoint before looping"
            )

        while True:
            try:
                monitor_once()
            except Exception:
                traceback.print_exc()

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

    if not (
        args.scan
        or args.once
        or args.loop
    ):
        parser.print_help()


if __name__ == "__main__":
    main()
