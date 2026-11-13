
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
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from chart_indicators import describe as describe_chart
from sahmk_intelligence import SahmkProClient
from saudi_ema_stack import evaluate as evaluate_ema_retest
from telegram_delivery import broadcast

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
EMA_SCAN_LIMIT = 10
EMA_SCAN_BUDGET_SECONDS = 45


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

        if find_value(data, ("is_delayed",)) is not False:
            return None, "realtime_not_verified"

        price_raw = find_value(
            data,
            ("last", "last_price", "lastPrice",
             "price", "close", "lastTradePrice")
        )

        if price_raw is None:
            return None, "price_missing"

        price = float(str(price_raw).replace(",", ""))

        if not math.isfinite(price) or price <= 0:
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

        turnover_raw = find_value(data, ("turnover", "traded_value", "total_value"))
        try:
            turnover = float(str(turnover_raw).replace(",", ""))
        except (ValueError, TypeError):
            turnover = 0.0
        return {
            "symbol": symbol,
            "price": price,
            "turnover": turnover if math.isfinite(turnover) and turnover > 0 else 0.0,
            "timestamp": stamp.isoformat(),
            "age_seconds": round(age, 1),
        }, None

    except requests.HTTPError as exc:
        return None, f"http_error_{exc.response.status_code}"
    except (requests.RequestException, ValueError, TypeError) as exc:
        return None, str(exc)


def telegram_send(message):
    try:
        result = broadcast(message)
        return bool(result) and all(result.values())
    except Exception as exc:
        LOG.error("Telegram error: %s", type(exc).__name__)
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


def company_display_name(symbol, stock):
    """Prefer a supplied Arabic company name, never invent one."""
    for key in ("name_ar", "company_name_ar", "اسم_الشركة", "اسم الشركة",
                "company_name", "name", "name_en"):
        name = stock.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return str(symbol)


def evaluate(symbol, stock, quote):
    display_name = company_display_name(symbol, stock)
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
                    f"الشركة: {display_name} ({symbol})\n"
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
                    f"الشركة: {display_name} ({symbol})\n"
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
                f"الشركة: {display_name} ({symbol})\n"
                f"السعر: {price:.2f}\n"
                f"المقاومة: {resistance:.2f}\n"
                f"إشارات تاريخية: {len(strategies)}\n"
                f"يلزم تأكيد حجم التداول والثبات."
            ),
        })

    # A higher historical support already broken is not a separate alert.
    # Prefer the currently touched zone, then the nearest broken support.
    touches = [e for e in events if e["type"] == "SUPPORT_TOUCH"]
    breaks = [e for e in events if e["type"] == "SUPPORT_BREAK"]
    if touches:
        chosen = min(touches, key=lambda e: abs(e["level"] - price))
    elif breaks:
        chosen = min(breaks, key=lambda e: e["level"])
    elif events:
        chosen = events[0]
    else:
        return []
    chosen["key"] = f"{symbol}:{chosen['type']}:{chosen['level']:.4f}"
    label = {"SUPPORT_TOUCH": "🔎 مراقبة دعم — الدخول غير مؤكد",
             "SUPPORT_BREAK": "⚠️ ضعف فني — كسر دعم",
             "BREAKOUT_WATCH": "📈 اختراق مبدئي — للمراقبة"}[chosen["type"]]
    level_label = "المقاومة" if chosen["type"] == "BREAKOUT_WATCH" else "أقرب دعم مرجعي"
    gap = (price / chosen["level"] - 1) * 100
    condition = {
        "SUPPORT_TOUCH": "التأكيد: ارتداد بشمعة 15 دقيقة نهائية وحجم داعم؛ اللمس وحده ليس دخولًا.",
        "SUPPORT_BREAK": "الحالة: لا دخول اعتمادًا على هذا الدعم؛ يلزم استعادته وتأكيد الثبات.",
        "BREAKOUT_WATCH": "التأكيد: إغلاق 15 دقيقة نهائي أعلى المقاومة وحجم داعم؛ تجنّب المطاردة.",
    }[chosen["type"]]
    chosen["message"] = (f"{label}\nالشركة: {display_name} ({symbol})\n"
                         f"السعر: {price:.2f} ريال\n{level_label}: {chosen['level']:.2f} ريال ({gap:+.2f}%)\n"
                         f"{condition}\nالدخول غير مؤكد؛ قراءة السعر وحدها لا تكفي.")
    return [chosen]


def price_time_label(value):
    stamp = parse_timestamp(value)
    return stamp.astimezone(RIYADH).strftime("%d/%m/%Y • %H:%M:%S بتوقيت الرياض") if stamp else "وقت غير متحقق"


def process_events(events, state, quote):
    changed = False
    history = read_json(EVENTS_FILE, [])
    if not isinstance(history, list):
        history = []

    current = now_utc()

    for event in events:
        key = event["key"]
        # Persist a daily semantic fingerprint, independent of quote/zone index.
        fingerprint_key = f"last_alert:{quote['symbol']}"
        if event["type"] == "EMA_STACK_RETEST":
            fingerprint_key += ":ema_stack"
        fingerprint = {"date": current.astimezone(RIYADH).date().isoformat(),
                       "type": event["type"], "level": event.get("level")}
        if state.get(fingerprint_key) == fingerprint:
            continue
        last = parse_timestamp(state.get(key))

        if last is not None:
            elapsed = (current - last).total_seconds()
            if elapsed < ALERT_COOLDOWN:
                continue

        message = (
            event["message"]
            + f"\n🕒 {price_time_label(quote['timestamp'])}\nالمصدر: سهمك Pro • تنبيه معلوماتي وليس أمر بيع أو شراء"
        )

        delivered = telegram_send(message)
        if not delivered:
            LOG.error("V10 alert delivery failed for %s; cooldown NOT recorded", quote["symbol"])
            continue

        record = {
            "time": current.isoformat(),
            "symbol": quote["symbol"],
            "price": quote["price"],
            "type": event["type"],
            "telegram_sent": delivered,
        }

        history.append(record)
        state[key] = current.isoformat()
        state[fingerprint_key] = fingerprint
        changed = True
        LOG.info("%s", message.replace("\n", " | "))

    if changed:
        save_json(STATE_FILE, state)
        save_json(EVENTS_FILE, history[-1000:])

    return changed


def ema_market_positive(payload, now):
    if find_value(payload, ("is_delayed",)) is not False:
        return False
    stamp = parse_timestamp(find_value(payload, ("updated_at", "timestamp", "last_update", "index_updated_at")))
    if stamp is None or not -60 <= (now - stamp).total_seconds() <= MAX_QUOTE_AGE:
        return False
    try:
        change = float(find_value(payload, ("index_change_percent", "change_percent")))
        advancing = int(find_value(payload, ("advancing", "gainers")))
        declining = int(find_value(payload, ("declining", "losers")))
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(change) and change > 0 and declining >= 0 and advancing > 1.4 * declining


def scan_ema_alerts(s, candidates, state):
    local = now_saudi()
    if not candidates or not market_open() or local.hour < 11:
        return 0
    client = SahmkProClient(session=s)
    try:
        market = client.market()
        if not ema_market_positive(market, now_utc()):
            LOG.info("EMA STACK HOLD | TASI not verified positive")
            return 0
    except Exception as exc:
        LOG.warning("EMA STACK HOLD | TASI error=%s", type(exc).__name__)
        return 0

    started = time.monotonic()
    ranked = sorted(candidates, key=lambda item: item[1].get("turnover", 0), reverse=True)
    sent = 0
    for stock, quote in ranked[:EMA_SCAN_LIMIT]:
        if time.monotonic() - started >= EMA_SCAN_BUDGET_SECONDS:
            break
        symbol = quote["symbol"]
        try:
            hourly = client.intraday(symbol, "60min", recent=True)
            fifteen = client.intraday(symbol, "15min", recent=True)
            stamp = parse_timestamp(quote["timestamp"])
            current = now_utc()
            latest_close = current.replace(minute=current.minute // 15 * 15, second=0, microsecond=0)
            if stamp is None or stamp < latest_close or (current - stamp).total_seconds() > MAX_QUOTE_AGE:
                quote, error = fetch_quote(s, symbol)
                if error:
                    continue
                stamp = parse_timestamp(quote["timestamp"])
            if stamp is None:
                continue
            setup = evaluate_ema_retest(hourly, fifteen, quote["price"], stamp, now_utc())
        except Exception as exc:
            LOG.warning("EMA STACK DATA HOLD | symbol=%s error=%s", symbol, type(exc).__name__)
            continue
        if setup is None:
            continue
        if not ema_market_positive(market, now_utc()):
            break
        event = {
            "type": "EMA_STACK_RETEST",
            "key": f"{symbol}:EMA_STACK_RETEST:{setup.confirmed_at.isoformat()}",
            "level": round(setup.entry, 2),
            "chart_indicators": setup.chart_indicators,
            "message": (
                "📈 Saudi AI Sniper | إعادة اختبار EMA 25\n"
                f"الشركة: {company_display_name(symbol, stock)} ({symbol})\n"
                f"السعر: {quote['price']:.2f} ريال\n"
                f"خط الدخول المشروط: {setup.entry:.2f} ريال\n"
                f"أقرب دعم ووقف تقديري: {setup.stop:.2f} ريال\n"
                f"فيبو تصحيحي {setup.fib.ratio * 100:.1f}% عند {setup.fib.retracement:.2f} ريال\n"
                f"امتداد 138.2% / 150% / 161.8%: {' / '.join(f'{level:.2f}' for level in setup.fib.extensions)} ريال\n"
                f"فيبو الشارت 0/23.6/38.2/78.6/100 وامتداد ثلاثي 127.2%: {describe_chart(setup.chart_indicators['1h'])}\n"
                f"EMA 25/75/140 صاعدة على الساعة و15د | RSI 14 صاعد {setup.strength:.1f}\n"
                f"تأكيد شمعة 15د: {setup.confirmed_at:%H:%M} بتوقيت الرياض | تاسي إيجابي\n"
                "مراقبة إعادة اختبار الخط."
            ),
        }
        sent += int(process_events([event], state, quote))
    return sent


def scan_once(force=False):
    if not force and not market_open():
        LOG.info("Saudi market is closed; no live alerts")
        return

    metadata, stocks = load_watchlist()
    s = session()
    # Historical watchlists may contain only technical data, without company names.
    # Resolve missing names from the existing SAHMK TASI directory once per scan.
    unnamed = [symbol for symbol, stock in stocks.items()
               if company_display_name(symbol, stock) == str(symbol)]
    if unnamed:
        try:
            from market_scanner import tasi_equities
            names = tasi_equities(s)
            for symbol in unnamed:
                if names.get(symbol):
                    stocks[symbol]["name_ar"] = names[symbol]
            LOG.info("Company names resolved | missing=%d resolved=%d",
                     len(unnamed), sum(bool(names.get(symbol)) for symbol in unnamed))
        except Exception as exc:
            LOG.warning("Company directory unavailable; retaining tickers: %s", type(exc).__name__)
    state = read_json(STATE_FILE, {})

    total = len(stocks)
    valid = 0
    alerts = 0
    errors = {}
    ema_candidates = []

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
        ema_candidates.append((stock, quote))
        events = evaluate(symbol, stock, quote)

        if events and process_events(events, state, quote):
            alerts += 1

    ema_alerts = scan_ema_alerts(s, ema_candidates, state)
    LOG.info(
        "Completed | watched=%d | fresh_quotes=%d | "
        "stocks_with_alerts=%d | ema_alerts=%d | errors=%d",
        total, valid, alerts, ema_alerts, len(errors),
    )

    if errors:
        LOG.warning(
            "Quote errors: %s",
            json.dumps(errors, ensure_ascii=False),
        )

    if valid == 0:
        raise RuntimeError(
            "NO VERIFIED LIVE QUOTES. Check SAHMK endpoint, authentication, "
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


