"""Saudi AI Sniper — live watcher (read-only).
Historical setups are previews, never live buy recommendations.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, time as clock_time
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from websocket import WebSocketTimeoutException, create_connection
from market_scanner import run
from sahmk_pro import tasi_market
from telegram_scan import send_telegram, strategy_label

TZ = ZoneInfo('Asia/Riyadh')
LOG = logging.getLogger('sniper-live')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
WS_URL = 'wss://api.sahmk.sa/ws/v1/stocks/?api_key='
REFRESH_SECONDS = max(900, int(os.getenv('SETUP_REFRESH_SECONDS', '1800')))
MARKET_REFRESH_SECONDS = 180
STATE = Path('live_alert_state.json')
lock = threading.RLock()
watchlist = {}
market = None
last_prices = {}
sent = set()
failures = set()


def market_open(now):
    return now.weekday() in (6, 0, 1, 2, 3) and clock_time(10) <= now.time() < clock_time(15)


def number(value):
    try:
        x = float(value)
        return x if 0 < x < float('inf') else None
    except (TypeError, ValueError, OverflowError):
        return None


def load_sent():
    try:
        data = json.loads(STATE.read_text(encoding='utf-8'))
        return set(map(str, data)) if isinstance(data, list) else set()
    except (OSError, ValueError):
        return set()


def save_sent():
    try:
        temp = STATE.with_suffix('.tmp')
        temp.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding='utf-8')
        temp.replace(STATE)
    except OSError:
        LOG.exception('Cannot save alert state')


def notify(token, chat_id, message):
    try:
        return send_telegram(token, chat_id, message) is not False
    except Exception:
        LOG.exception('Telegram delivery failed')
        return False


def issue(token, chat_id, category, exc):
    with lock:
        if category in failures:
            return
        failures.add(category)
    LOG.warning('%s failed: %s', category, exc)
    notify(token, chat_id, f'⚠️ Saudi AI Sniper | تعطل {category}\n{type(exc).__name__}: {str(exc)[:130]}')


def recovered(token, chat_id, category):
    with lock:
        if category not in failures:
            return
        failures.remove(category)
    notify(token, chat_id, f'✅ Saudi AI Sniper | عاد {category} للعمل.')


def market_status():
    data = tasi_market()
    if not isinstance(data, dict) or data.get('is_delayed') is not False:
        raise ValueError('TASI data not confirmed real-time')
    if any(data.get(k) is None for k in ('index_change_percent', 'advancing', 'declining')):
        raise ValueError('TASI breadth fields missing')
    change = float(data['index_change_percent'])
    advancing = int(data['advancing'])
    declining = int(data['declining'])
    if advancing + declining <= 0:
        raise ValueError('TASI breadth empty')
    return {'positive': change > 0 and advancing > 1.4 * declining, 'change': change}


def prepare(report):
    """Watch liquid ranked stocks; keep entry criteria separate from monitoring."""
    rows = report.get('historical_candidates') or []
    if not isinstance(rows, list):
        raise ValueError('Invalid historical_candidates')
    prepared = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get('السهم') or '').strip()
        if not symbol.isdigit():
            continue
        if row.get('مستوفي_شرط_السيولة') is not True:
            continue
        prepared[symbol] = row
    return prepared


def valid_setup(row):
    if row.get('تصنيف_الماسح') != 'فرصة مشروطة':
        return False
    entry = number(row.get('بداية_نطاق_الشراء'))
    stop = number(row.get('وقف_تقديري'))
    if entry is None or stop is None or stop >= entry:
        return False
    try:
        candle_date = datetime.fromisoformat(str(row.get('التاريخ') or '')[:10]).date()
    except ValueError:
        return False
    weekly = str(row.get('الإطار_الزمني') or '').lower() in ('weekly', '1w', 'أسبوعي')
    age = (datetime.now(TZ).date() - candle_date).days
    return 0 <= age <= (10 if weekly else 5)


def send_previews(rows, token, chat_id):
    for symbol, row in list(rows.items()):
        if not valid_setup(row):
            continue
        strategy = str(row.get('الاستراتيجيات') or '')
        candle = str(row.get('التاريخ') or '')[:10]
        unique = f'PREVIEW|{symbol}|{strategy}|{candle}'
        with lock:
            if unique in sent:
                continue
        message = (
            '🇸🇦 Saudi AI Sniper | فرصة مراقبة تاريخية\n\n'
            f'{strategy_label(strategy)}\n'
            f"السهم: {row.get('اسم_الشركة', symbol)} ({symbol})\n"
            f"مستوى التفعيل: {row.get('بداية_نطاق_الشراء')} ريال\n"
            f"وقف تقديري: {row.get('وقف_تقديري')} ريال\n"
            f"الهدف الأول: {row.get('هدف_تقديري')} ريال\n"
            f'تاريخ الشمعة: {candle}\n\n'
            '⚠️ تحليل تاريخي فقط، ليس تأكيد دخول لحظي.'
        )
        if notify(token, chat_id, message):
            with lock:
                sent.add(unique)
                save_sent()


def refresh_loop(token, chat_id):
    global watchlist, market
    last_scan = 0
    last_market = 0
    last_heartbeat = 0
    last_report_day = None
    while True:
        now = time.monotonic()
        local = datetime.now(TZ)
        session = market_open(local)
        if now - last_heartbeat >= 60:
            with lock:
                count = len(watchlist)
                qualified = sum(valid_setup(row) for row in watchlist.values())
            LOG.info('HEARTBEAT alive; market_open=%s; watched=%d; qualified=%d', session, count, qualified)
            last_heartbeat = now
        if now - last_scan >= REFRESH_SECONDS:
            last_scan = now
            started = time.monotonic()
            LOG.info('SCAN START: fetching TASI history')
            try:
                report = run(max_candidates=50, quote_budget=0)
                fresh = prepare(report)
                stats = report.get('summary') or {}
                # Preserve a working watchlist if a later scan returns zero unexpectedly.
                with lock:
                    if fresh:
                        watchlist = fresh
                    current_count = len(watchlist)
                LOG.info('SCAN DONE: universe=%s analyzed=%s failed=%s liquid_watch=%d qualified=%d duration=%ss',
                         report.get('universe_size', 0), stats.get('analyzed', 0),
                         stats.get('failed_total', 0), current_count,
                         sum(valid_setup(row) for row in fresh.values()), int(time.monotonic() - started))
                if not fresh:
                    raise RuntimeError('Scanner returned no liquid watchlist stocks; inspect historical data and liquidity filter')
                recovered(token, chat_id, 'الفحص التاريخي')
                send_previews(fresh, token, chat_id)
            except Exception as exc:
                LOG.exception('SCAN FAILED')
                issue(token, chat_id, 'الفحص التاريخي', exc)
        if session and now - last_market >= MARKET_REFRESH_SECONDS:
            last_market = now
            try:
                fresh_market = market_status()
                with lock:
                    market = fresh_market
                LOG.info('TASI change=%+.2f%% positive=%s', fresh_market['change'], fresh_market['positive'])
                recovered(token, chat_id, 'بيانات تاسي اللحظية')
            except Exception as exc:
                with lock:
                    market = None
                issue(token, chat_id, 'بيانات تاسي اللحظية', exc)
        if session and last_report_day != local.date() and local.time() >= clock_time(10, 15):
            with lock:
                count = len(watchlist)
                qualified = sum(valid_setup(row) for row in watchlist.values())
                m = market
            if notify(token, chat_id, f'📊 Saudi AI Sniper | تقرير المراقبة\nالأسهم المراقبة: {count}\nفرص تاريخية مستوفية: {qualified}\nفلتر تاسي إيجابي: {"نعم" if m and m["positive"] else "لا / غير متاح"}\n⚠️ عدد المراقبة ليس عدد إشارات الدخول.'):
                last_report_day = local.date()
        if not session:
            with lock:
                market = None
        time.sleep(5)


def on_quote(message, token, chat_id):
    if message.get('type') != 'quote':
        return
    data = message.get('data') or {}
    if not isinstance(data, dict) or data.get('is_delayed') is not False:
        return
    symbol = str(message.get('symbol') or data.get('symbol') or '').strip()
    price = number(data.get('price') or data.get('last_price') or message.get('price'))
    if not symbol or price is None or not market_open(datetime.now(TZ)):
        return
    with lock:
        row = watchlist.get(symbol)
        current_market = market
        previous = last_prices.get(symbol)
        last_prices[symbol] = price
    if row is None or not valid_setup(row):
        return
    entry = number(row.get('بداية_نطاق_الشراء'))
    if not current_market or not current_market['positive'] or entry is None or previous is None:
        return
    if not (previous < entry <= price):
        return
    strategy = str(row.get('الاستراتيجيات') or '')
    candle = str(row.get('التاريخ') or '')[:10]
    unique = f'LIVE|{symbol}|{strategy}|{candle}'
    with lock:
        if unique in sent:
            return
    message_text = (
        '🇸🇦 Saudi AI Sniper | 🚨 عبور سعري لحظي\n\n'
        f'{strategy_label(strategy)}\n'
        f"السهم: {row.get('اسم_الشركة', symbol)} ({symbol})\n"
        f'السعر المستلم: {price:.3f} ريال\n'
        f'مستوى التفعيل: {entry:.3f} ريال\n'
        f"وقف تقديري: {row.get('وقف_تقديري')} ريال\n"
        f"الهدف الأول: {row.get('هدف_تقديري')} ريال\n"
        f"الهدف الثاني: {row.get('هدف_ثان')} ريال\n"
        f"تاسي: {current_market['change']:+.2f}%\n\n"
        '⚠️ عبور سعر فقط؛ تحقق من السيولة وعمق السوق قبل القرار.'
    )
    if notify(token, chat_id, message_text):
        with lock:
            sent.add(unique)
            save_sent()
        LOG.info('LIVE ALERT SENT: %s %.3f', symbol, price)


def main():
    key = os.getenv('SAHMK_API_KEY', '').strip()
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat_id = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not all((key, token, chat_id)):
        raise RuntimeError('Missing SAHMK_API_KEY / TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID')
    global sent
    sent = load_sent()
    LOG.info('Saudi AI Sniper — separated monitoring and alerts started')
    threading.Thread(target=refresh_loop, args=(token, chat_id), daemon=True).start()
    while True:
        if not market_open(datetime.now(TZ)):
            time.sleep(30)
            continue
        with lock:
            symbols = list(watchlist.keys())[:60]
        if not symbols:
            time.sleep(15)
            continue
        ws = None
        try:
            ws = create_connection(WS_URL + quote(key, safe=''), timeout=15)
            ws.settimeout(10)
            subscribed = False
            started = time.monotonic()
            while market_open(datetime.now(TZ)) and time.monotonic() - started <= 300:
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue
                message = json.loads(raw)
                event = message.get('type')
                if event == 'connected' and not subscribed:
                    limits = message.get('limits') or {}
                    max_symbols = max(1, min(60, int(limits.get('max_symbols_per_connection') or 60)))
                    per_call = max(1, min(20, int(limits.get('max_symbols_per_call') or 20)))
                    count = min(len(symbols), max_symbols)
                    for i in range(0, count, per_call):
                        ws.send(json.dumps({'action': 'subscribe', 'symbols': symbols[i:i + per_call]}))
                    subscribed = True
                    LOG.info('Subscribed to %d symbols (provider limits respected)', count)
                elif event == 'error':
                    raise RuntimeError('WebSocket server error: ' + str(message.get('code', 'unknown')))
                elif event == 'quote':
                    try:
                        on_quote(message, token, chat_id)
                    except Exception:
                        LOG.exception('Quote processing failed')
            if subscribed:
                recovered(token, chat_id, 'اتصال الأسعار اللحظية')
            else:
                raise RuntimeError('WebSocket did not confirm connection/subscription')
        except Exception as exc:
            LOG.exception('WebSocket connection failed')
            issue(token, chat_id, 'اتصال الأسعار اللحظية', exc)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        time.sleep(10)


if __name__ == '__main__':
    main()
