"""Saudi AI Sniper V5.1 — diagnostic historical scanner and live watcher.
Read-only; no orders. Market data must be verified before acting.
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
lock = threading.Lock()
setups = {}
market = None
last_prices = {}
sent = set()


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
        return set(str(x) for x in data) if isinstance(data, list) else set()
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
        send_telegram(token, chat_id, message)
        return True
    except Exception:
        LOG.exception('Telegram delivery failed')
        return False


def market_status():
    data = tasi_market()
    if not isinstance(data, dict) or data.get('is_delayed') is not False:
        raise ValueError('TASI data not confirmed real-time')
    required = ('index_change_percent', 'advancing', 'declining')
    if any(data.get(k) is None for k in required):
        raise ValueError('TASI breadth fields missing')
    change = float(data['index_change_percent'])
    advancing, declining = int(data['advancing']), int(data['declining'])
    if advancing + declining <= 0:
        raise ValueError('TASI breadth empty')
    return {'positive': change > 0 and advancing > 1.4 * declining, 'change': change}


def prepare(report):
    rows = report.get('historical_candidates') or []
    if not isinstance(rows, list):
        raise ValueError('Invalid historical_candidates')
    today = datetime.now(TZ).date()
    prepared = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('تصنيف_الماسح') != 'فرصة مشروطة':
            continue
        symbol = str(row.get('السهم') or '').strip()
        entry = number(row.get('بداية_نطاق_الشراء'))
        stop = number(row.get('وقف_تقديري'))
        if not symbol.isdigit() or entry is None or stop is None or stop >= entry:
            continue
        try:
            date = datetime.fromisoformat(str(row.get('التاريخ') or '')[:10]).date()
        except ValueError:
            continue
        weekly = str(row.get('الإطار_الزمني') or '').lower() in ('weekly', '1w', 'أسبوعي')
        age = (today - date).days
        if 0 <= age <= (10 if weekly else 5):
            prepared[symbol] = row
    return prepared


def send_previews(rows, token, chat_id):
    today = datetime.now(TZ).date().isoformat()
    for symbol, row in list(rows.items())[:10]:
        strategy = str(row.get('الاستراتيجيات') or '')
        candle = str(row.get('التاريخ') or '')[:10]
        unique = f'PREVIEW|{symbol}|{strategy}|{candle}|{today}'
        if unique in sent:
            continue
        message = (
            '🇸🇦 Saudi AI Sniper V5.1\n📊 فرصة للمراقبة — ليست دخولًا مؤكدًا\n\n'
            f'{strategy_label(strategy)}\n'
            f"السهم: {row.get('اسم_الشركة', symbol)} ({symbol})\n"
            f"مستوى التفعيل: {row.get('بداية_نطاق_الشراء')} ريال\n"
            f"وقف تقديري: {row.get('وقف_تقديري')} ريال\n"
            f"الهدف الأول: {row.get('هدف_تقديري')} ريال\n"
            f"الهدف الثاني: {row.get('هدف_ثان')} ريال\n"
            f'تاريخ الشمعة: {candle}\n\n'
            '⚠️ بيانات تاريخية، وليست توصية تنفيذ أو تأكيدًا للسعر اللحظي.'
        )
        if notify(token, chat_id, message):
            sent.add(unique)
            save_sent()
            LOG.info('Preview sent: %s', symbol)


def refresh_loop(token, chat_id):
    global setups, market
    last_scan = 0
    last_market = 0
    last_heartbeat = 0
    while True:
        now = time.monotonic()
        session = market_open(datetime.now(TZ))
        if now - last_heartbeat >= 60:
            with lock:
                count = len(setups)
            LOG.info('HEARTBEAT alive; market_open=%s; watched=%d', session, count)
            last_heartbeat = now
        if now - last_scan >= REFRESH_SECONDS:
            last_scan = now
            LOG.info('SCAN START: fetching TASI history')
            started = time.monotonic()
            try:
                report = run(max_candidates=50, quote_budget=0)
                fresh = prepare(report)
                with lock:
                    setups = fresh
                stats = report.get('summary') or {}
                elapsed = int(time.monotonic() - started)
                discovered = report.get('universe_size', 0)
                analyzed = stats.get('analyzed', 0)
                failed = stats.get('failed_total', 0)
                LOG.info('SCAN DONE: universe=%s analyzed=%s failed=%s valid=%s duration=%ss',
                         discovered, analyzed, failed, len(fresh), elapsed)
                notify(token, chat_id,
                       '🇸🇦 Saudi AI Sniper V5.1 | تقرير الفحص\n'
                       f'أسهم تاسي: {discovered}\n'
                       f'تم تحليلها: {analyzed}\n'
                       f'فشل جلب/تحليل: {failed}\n'
                       f'فرص مراقبة مستوفية: {len(fresh)}\n'
                       f'مدة الفحص: {elapsed} ثانية\n'
                       '⚠️ نتائج تاريخية وليست إشارات دخول لحظية.')
                send_previews(fresh, token, chat_id)
            except Exception as exc:
                LOG.exception('SCAN FAILED')
                notify(token, chat_id,
                       f'⚠️ Saudi AI Sniper V5.1: فشل الفحص\n'
                       f'{type(exc).__name__}: {str(exc)[:180]}')
        if session and now - last_market >= MARKET_REFRESH_SECONDS:
            last_market = now
            try:
                fresh_market = market_status()
                with lock:
                    market = fresh_market
                LOG.info('TASI change=%+.2f%% positive=%s', fresh_market['change'], fresh_market['positive'])
            except Exception as exc:
                with lock:
                    market = None
                LOG.warning('TASI live unavailable: %s', exc)
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
        row = setups.get(symbol)
        current_market = market
    if row is None:
        return
    previous = last_prices.get(symbol)
    last_prices[symbol] = price
    entry = number(row.get('بداية_نطاق_الشراء'))
    if not current_market or not current_market['positive'] or entry is None or previous is None:
        return
    if not (previous < entry <= price):
        return
    strategy = str(row.get('الاستراتيجيات') or '')
    candle = str(row.get('التاريخ') or '')[:10]
    unique = f'LIVE|{symbol}|{strategy}|{candle}'
    if unique in sent:
        return
    message = (
        '🇸🇦 Saudi AI Sniper V5.1\n🚨 عبور لحظي مشروط\n\n'
        f'{strategy_label(strategy)}\n'
        f"السهم: {row.get('اسم_الشركة', symbol)} ({symbol})\n"
        f'السعر المستلم: {price:.3f} ريال\n'
        f'مستوى التفعيل: {entry:.3f} ريال\n'
        f"وقف تقديري: {row.get('وقف_تقديري')} ريال\n"
        f"الهدف الأول: {row.get('هدف_تقديري')} ريال\n"
        f"الهدف الثاني: {row.get('هدف_ثان')} ريال\n"
        f"تاسي: {current_market['change']:+.2f}%\n\n"
        '⚠️ عبور سعري فقط؛ تحقق من السيولة وعمق السوق قبل أي قرار.'
    )
    if notify(token, chat_id, message):
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
    LOG.info('Saudi AI Sniper V5.1 started')
    notify(token, chat_id, '✅ Saudi AI Sniper V5.1 بدأ التشغيل. جاري فحص البيانات التاريخية؛ انتظر تقرير النتائج.')
    threading.Thread(target=refresh_loop, args=(token, chat_id), daemon=True).start()
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
                    LOG.info('Subscribed to %d symbols', count)
                elif event == 'error':
                    LOG.warning('WebSocket server error: %s', message.get('code', 'unknown'))
                    break
                elif event == 'quote':
                    try:
                        on_quote(message, token, chat_id)
                    except Exception:
                        LOG.exception('Quote processing failed')
        except Exception:
            LOG.exception('WebSocket connection failed')
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
        time.sleep(10)


if __name__ == '__main__':
    main()
