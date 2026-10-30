"""Saudi AI Sniper V5 — live-trigger monitor (read-only).

Historical scanner supplies PRELIMINARY setups. A live WebSocket price crossing
is required for a Telegram alert. This is not a trading/execution engine.
Run: python -u live_worker.py
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote

from websocket import WebSocketTimeoutException, create_connection
from market_scanner import run
from sahmk_pro import tasi_market
from telegram_scan import send_telegram, strategy_label

TZ = ZoneInfo("Asia/Riyadh")
LOG = logging.getLogger("sniper-live")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
WS_URL = "wss://api.sahmk.sa/ws/v1/stocks/?api_key="
STATE = Path("live_alert_state.json")
REFRESH_SECONDS = max(900, int(os.getenv("SETUP_REFRESH_SECONDS", "1800")))
MARKET_REFRESH_SECONDS = 180

setups = {}
market = None
lock = threading.Lock()
last_prices = {}
sent = set()


def market_open(now):
    # Regular continuous session only: no auction, weekends, or holiday handling.
    return now.weekday() in (6, 0, 1, 2, 3) and clock_time(10) <= now.time() < clock_time(15)


def number(value):
    try:
        result = float(value)
        return result if 0 < result < float("inf") else None
    except (ValueError, TypeError, OverflowError):
        return None


def load_sent():
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return set(str(x) for x in data)
    except (OSError, ValueError):
        pass
    return set()


def save_sent():
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding="utf-8")
    tmp.replace(STATE)


def status():
    data = tasi_market()
    required = ("index_change_percent", "advancing", "declining")
    if any(data.get(k) is None for k in required):
        raise ValueError("Missing TASI breadth data")
    if data.get("is_delayed") is not False:
        raise ValueError("TASI feed not confirmed non-delayed")
    change = float(data["index_change_percent"])
    advancing, declining = int(data["advancing"]), int(data["declining"])
    if advancing + declining == 0:
        raise ValueError("Empty TASI market breadth")
    positive = change > 0 and advancing > 1.4 * declining
    return {"positive": positive, "change": change}


def read_setups():
    # Existing scanner evaluates historical daily and, if implemented, weekly setups.
    report = run(max_candidates=50, quote_budget=0)
    rows = list(report.get("historical_candidates") or [])
    for key in ("weekly_candidates", "investment_candidates"):
        extra = report.get(key)
        if isinstance(extra, list):
            rows.extend(extra)
    prepared = {}
    today = datetime.now(TZ).date()
    for row in rows:
        if row.get("تصنيف_الماسح") != "فرصة مشروطة":
            continue
        symbol = str(row.get("السهم", "")).strip()
        entry = number(row.get("بداية_نطاق_الشراء"))
        stop = number(row.get("وقف_تقديري"))
        if not symbol.isdigit() or entry is None or stop is None or stop >= entry:
            continue
        try:
            date = datetime.fromisoformat(str(row.get("التاريخ", ""))[:10]).date()
        except ValueError:
            continue
        weekly = str(row.get("الإطار_الزمني", "")).lower() in ("weekly", "1w", "أسبوعي")
        age = (today - date).days
        if age < 0 or age > (10 if weekly else 5):
            continue
        prepared[symbol] = row
    LOG.info("Loaded %d validated historical setups for live monitoring", len(prepared))
    return prepared


def refresh_loop():
    global market, setups
    last_market = 0
    last_setups = 0
    while True:
        if market_open(datetime.now(TZ)):
            now = time.monotonic()
            if now - last_market >= MARKET_REFRESH_SECONDS:
                last_market = now
                try:
                    fresh = status()
                    with lock:
                        market = fresh
                    LOG.info("TASI change=%+.2f%%, bullish=%s", fresh["change"], fresh["positive"])
                except Exception as exc:
                    with lock:
                        market = None  # Fail closed: no alerts if TASI unavailable.
                    LOG.warning("TASI unavailable: %s", exc)
            if now - last_setups >= REFRESH_SECONDS:
                last_setups = now
                try:
                    fresh = read_setups()
                    with lock:
                        setups = fresh
                except Exception:
                    LOG.exception("Historical setup refresh failed")
        time.sleep(5)


def on_quote(message, token, chat_id):
    # Handle stock quote event in the format proven by the test workflow.
    if message.get("type") != "quote":
        return
    data = message.get("data") or {}
    if not isinstance(data, dict):
        return
    symbol = str(message.get("symbol") or data.get("symbol") or "").strip()
    price = number(data.get("price") or data.get("last_price") or message.get("price"))
    if not symbol or price is None or data.get("is_delayed") is True:
        return
    if not market_open(datetime.now(TZ)):
        return
    with lock:
        row = setups.get(symbol)
        current_market = market
    if row is None:
        return
    # Observe a fresh quote at/below trigger first; don't send on startup if
    # the share was already trading above the suggested entry.
    previous = last_prices.get(symbol)
    last_prices[symbol] = price
    entry = number(row.get("بداية_نطاق_الشراء"))
    if not current_market or not current_market["positive"] or entry is None:
        return
    if previous is None or not (previous < entry <= price):
        return
    candle = str(row.get("التاريخ"))[:10]
    strategy = str(row.get("الاستراتيجيات") or "")
    unique = f"{symbol}|{strategy}|{candle}"
    if unique in sent:
        return
    text = (
        "🇸🇦 Saudi AI Sniper V5 — live crossing detected\n"
        f"{strategy_label(strategy)}\n"
        f"السهم: {row.get('اسم_الشركة', symbol)} ({symbol})\n"
        f"السعر المستلم: {price:.3f} ريال\n"
        f"سعر التفعيل التاريخي: {entry:.3f} ريال\n"
        f"وقف تقديري: {row.get('وقف_تقديري')} ريال\n"
        f"هدف أول: {row.get('هدف_تقديري')} ريال\n"
        f"هدف ثانٍ: {row.get('هدف_ثان')} ريال\n"
        f"تاسي: {current_market['change']:+.2f}%\n"
        "⚠️ عبور سعر لمستوى فني تاريخي؛ ليس تأكيدًا للسيولة أو صلاحية التنفيذ. "
        "راجع السوق وعمق الأوامر يدويًا. لا توجد أوامر تداول آلية."
    )
    send_telegram(token, chat_id, text)
    sent.add(unique)
    save_sent()
    LOG.info("Alert sent: %s at %.3f", symbol, price)


def main():
    key = os.getenv("SAHMK_API_KEY", "").strip()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not all((key, token, chat_id)):
        raise RuntimeError("Missing SAHMK_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")
    global sent
    sent = load_sent()
    threading.Thread(target=refresh_loop, daemon=True).start()
    LOG.info("Live worker started. Waiting for regular TASI market session.")
    while True:
        if not market_open(datetime.now(TZ)):
            time.sleep(30)
            continue
        with lock:
            symbols = list(setups.keys())[:60]
        if not symbols:
            time.sleep(15)
            continue
        ws = None
        try:
            ws = create_connection(WS_URL + quote(key, safe=""), timeout=15)
            ws.settimeout(10)
            subscribed = False
            started = time.monotonic()
            while market_open(datetime.now(TZ)):
                # Reconnect periodically for a fresh watchlist subscription.
                if time.monotonic() - started > 300:
                    break
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                message = json.loads(raw)
                event = message.get("type")
                if event == "connected" and not subscribed:
                    limits = message.get("limits") or {}
                    max_symbols = min(60, int(limits.get("max_symbols_per_connection", 60)))
                    per_call = min(20, int(limits.get("max_symbols_per_call", 20)))
                    for i in range(0, min(len(symbols), max_symbols), per_call):
                        ws.send(json.dumps({"action": "subscribe", "symbols": symbols[i:i + per_call]}))
                    subscribed = True
                    LOG.info("Subscribed to %d symbols", min(len(symbols), max_symbols))
                elif event == "error":
                    LOG.warning("WebSocket server error: %s", message.get("code", "unknown"))
                    break
                elif event == "quote":
                    try:
                        on_quote(message, token, chat_id)
                    except Exception:
                        LOG.exception("Failed to evaluate/send quote alert")
        except Exception:
            LOG.exception("WebSocket failed; retrying")
        finally:
            if ws is not None:
                ws.close()
        time.sleep(10)


if __name__ == "__main__":
    main()
