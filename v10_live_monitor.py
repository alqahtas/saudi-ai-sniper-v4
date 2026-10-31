
"""Saudi AI Sniper V10.1 — Live Monitor (read-only).

Commands:
    python v10_live_monitor.py --once
    python v10_live_monitor.py --loop
    python v10_live_monitor.py --check

Requires v10_watchlist.json from the V10 historical scan.

No automatic orders. No fabricated prices.
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

LOG = logging.getLogger("V10")
RIYADH = timezone(timedelta(hours=3))

WATCHLIST = Path("v10_watchlist.json")
STATE_FILE = Path("v10_monitor_state.json")
EVENTS_FILE = Path("v10_events.json")

API_BASE = os.getenv(
    "SAHMK_BASE_URL",
    "https://api.sahmk.sa/api/v1"
).rstrip("/")

POLL_SECONDS = max(
    30, int(os.getenv("V10_POLL_SECONDS", "60"))
)

ALERT_COOLDOWN = 1800
MAX_QUOTE_AGE = 180
REQUEST_TIMEOUT = 15


def now_utc():
    return datetime.now(timezone.utc)


def now_saudi():
    return datetime.now(RIYADH)


def market_open():
    t = now_saudi()
    return (
        t.weekday() in (6, 0, 1, 2, 3)
        and (10, 0) <= (t.hour, t.minute) < (15, 0)
    )


def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOG.warning("Cannot read %s: %s", path, exc)
    return default


def save_json(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def load_watchlist():
    data = read_json(WATCHLIST, {})
    stocks = data.get("stocks", {})
    if not isinstance(stocks, dict) or not stocks:
        raise RuntimeError(
            "v10_watchlist.json missing or empty. "
            "Download the V10 scan artifact first."
        )
    return data, stocks


def session():
    """Reuse the project's SAHMK session when available."""
    try:
        from market_scanner import sahmk_session
        s = sahmk_session()
        if hasattr(s, "get"):
            return s
    except Exception as exc:
        LOG.warning("Project session unavailable: %s", exc)

    key = os.getenv("SAHMK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SAHMK_API_KEY is missing")

    s = requests.Session()
    s.headers.update({
        "X-API-Key": key,
        "Accept": "application/json",
    })
    return s


def parse_timestamp(value):
    if value is None:
        return None

    try:
        if isinstance(value, (int, float)):
            if value > 1e12:
                value /= 1000
            return datetime.fromtimestamp(value, timezone.utc)

        if isinstance(value, str):
            value = value.strip()
            if value.isdigit():
                return parse_timestamp(float(value))
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass

    return None


def find_value(data, keys):
    if not isinstance(data, dict):
        return None

    for key in keys:
        if key in data and data[key] is not None:
            return data[key]

    for wrapper in ("data", "quote", "result", "stock"):
        nested = data.get(wrapper)
        if isinstance(nested, dict):
            found = find_value(nested, keys)
            if found is not None:
                return found

    return None


def fetch_quote(s, symbol):
    """Use the existing SAHMK quote endpoint.

    A successful HTTP response is not proof of live data.
    Fresh provider timestamps are required for alerts.
    """
    url = f"{API_BASE}/quote/{symbol}/"

    try:
        response = s.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        price_raw = find_value(
            data,
            ("last", "last_price", "lastPrice",
             "price", "close", "lastTradePrice")
        )

        if price_raw is None:
            return None, "price_missing"

        price = float(str(price_raw).replace(",", ""))

        if price <= 0:
            return None, "invalid_price"

        timestamp_raw = find_value(
            data,
            ("timestamp", "updated_at", "updatedAt",
             "last_update", "lastUpdate",
             "trade_time", "tradeTime", "datetime")
        )

        stamp = parse_timestamp(timestamp_raw)

        if stamp is None:
            return None, "timestamp_missing"

        age = (now_utc() - stamp).total_seconds()

        if age < -60:
            return None, "future_timestamp"

        if age > MAX_QUOTE_AGE:
            return None, f"stale_quote_{int(age)}s"

        return {
            "symbol": symbol,
            "price": price,
            "timestamp": stamp.isoformat(),
            "age_seconds": round(age, 1),
        }, None

    except requests.HTTPError as exc:
        return None, f"http_error_{exc.response.status_code}"
    except (requests.RequestException, ValueError, TypeError) as exc:
        return None, str(exc)


def telegram_send(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        LOG.info("Telegram not configured; alert logged only")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
            },
            timeout=15,
        )
        response.raise_for_status()
        result = response.json()
        return bool(result.get("ok"))
    except (requests.RequestException, ValueError) as exc:
        LOG.error("Telegram error: %s", exc)
        return False


def valid_zones(stock):
    zones = stock.get("support_zones", [])
    result = []

    for zone in zones:
        try:
            bottom = float(zone["bottom"])
            top = float(zone["top"])

            if bottom > 0 and top >= bottom:
                result.append({
                    "bottom": bottom,
                    "top": top,
                    "confirmations": int(
                        zone.get("confirmations", 1)
                    ),
                })
        except (ValueError, TypeError, KeyError):
            continue

    return result


def evaluate(symbol, stock, quote):
    price = quote["price"]
    events = []

    for index, zone in enumerate(valid_zones(stock)):
        bottom = zone["bottom"]
        top = zone["top"]

        # Price near support, allowing a 0.3% proximity band.
        near_support = (
            bottom * 0.997 <= price <= top * 1.003
        )

        # Support broken by more than 0.5%.
        broken = price < bottom * 0.995

        if broken:
            events.append({
                "type": "SUPPORT_BREAK",
                "key": f"{symbol}:break:{index}",
                "level": bottom,
                "message": (
                    f"⚠️ V10 | كسر دعم\n"
                    f"السهم: {symbol}\n"
                    f"السعر: {price:.2f}\n"
                    f"الدعم: {bottom:.2f}\n"
                    f"تنبيه مخاطر، وليس أمر بيع."
                ),
            })
        elif near_support:
            events.append({
                "type": "SUPPORT_TOUCH",
                "key": f"{symbol}:touch:{index}",
                "level": bottom,
                "message": (
                    f"🔎 V10 | مراقبة ارتداد محتمل\n"
                    f"السهم: {symbol}\n"
                    f"السعر: {price:.2f}\n"
                    f"منطقة الدعم: {bottom:.2f} - {top:.2f}\n"
                    f"تأكيدات المنطقة: {zone['confirmations']}\n"
                    f"لم يتأكد الدخول بعد."
                ),
            })

    resistance = stock.get("resistance")
    strategies = stock.get("active_strategies") or []

    try:
        resistance = float(resistance)
    except (TypeError, ValueError):
        resistance = 0

    if (
        resistance > 0
        and price > resistance * 1.001
        and bool(stock.get("uptrend"))
        and len(strategies) > 0
    ):
        events.append({
            "type": "BREAKOUT_WATCH",
            "key": f"{symbol}:breakout",
            "level": resistance,
            "message": (
                f"📈 V10 | اختراق للمراقبة\n"
                f"السهم: {symbol}\n"
                f"السعر: {price:.2f}\n"
                f"المقاومة: {resistance:.2f}\n"
                f"إشارات تاريخية: {len(strategies)}\n"
                f"يلزم تأكيد حجم التداول والثبات."
            ),
        })

    return events


def process_events(events, state, quote):
    changed = False
    history = read_json(EVENTS_FILE, [])
    if not isinstance(history, list):
        history = []

    current = now_utc()

    for event in events:
        key = event["key"]
        last = parse_timestamp(state.get(key))

        if last is not None:
            elapsed = (current - last).total_seconds()
            if elapsed < ALERT_COOLDOWN:
                continue

        message = (
            event["message"]
            + f"\nوقت السعر: {quote['timestamp']}"
        )

        delivered = telegram_send(message)

        record = {
            "time": current.isoformat(),
            "symbol": quote["symbol"],
            "price": quote["price"],
            "type": event["type"],
            "telegram_sent": delivered,
        }

        history.append(record)
        state[key] = current.isoformat()
        changed = True
        LOG.info("%s", message.replace("\n", " | "))

    if changed:
        save_json(STATE_FILE, state)
        save_json(EVENTS_FILE, history[-1000:])

    return changed


def scan_once(force=False):
    if not force and not market_open():
        LOG.info("Saudi market is closed; no live alerts")
        return

    metadata, stocks = load_watchlist()
    s = session()
    state = read_json(STATE_FILE, {})

    total = len(stocks)
    valid = 0
    alerts = 0
    errors = {}

    LOG.info(
        "V10.1 started | universe=%s | watched=%s",
        metadata.get("universe_size", "unknown"),
        total,
    )

    for symbol, stock in stocks.items():
        quote, error = fetch_quote(s, symbol)

        if error:
            errors[symbol] = error
            continue

        valid += 1
        events = evaluate(symbol, stock, quote)

        if events and process_events(events, state, quote):
            alerts += 1

    LOG.info(
        "Completed | watched=%d | fresh_quotes=%d | "
        "stocks_with_alerts=%d | errors=%d",
        total, valid, alerts, len(errors),
    )

    if errors:
        LOG.warning(
            "Quote errors: %s",
            json.dumps(errors, ensure_ascii=False),
        )

    if valid == 0:
        LOG.error(
            "NO VERIFIED LIVE QUOTES. "
            "Check SAHMK endpoint, authentication, "
            "response fields and provider timestamps."
        )


def check_connection():
    _, stocks = load_watchlist()
    s = session()

    symbol = next(iter(stocks))
    quote, error = fetch_quote(s, symbol)

    if error:
        LOG.error(
            "Connection check failed | %s | %s",
            symbol, error,
        )
        return

    LOG.info(
        "Connection OK | symbol=%s | price=%s | "
        "provider_time=%s | age=%ss",
        symbol,
        quote["price"],
        quote["timestamp"],
        quote["age_seconds"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once", action="store_true",
        help="One monitoring pass during market hours"
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Continuous monitoring during market hours"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Test one SAHMK quote"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Allow one pass outside market hours"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    if args.check:
        check_connection()
    elif args.loop:
        LOG.info("Continuous V10.1 monitoring started")
        while True:
            try:
                scan_once(force=False)
            except Exception:
                LOG.exception("Monitoring cycle failed")
            time.sleep(POLL_SECONDS)
    else:
        scan_once(force=args.force)


if __name__ == "__main__":
    main()
