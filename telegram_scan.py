"""One Telegram sender; only new validated setups, once after market close.
This script NEVER places orders. Daily prices are delayed/historical.
"""
import os
from datetime import datetime, date
from zoneinfo import ZoneInfo
import requests
from sniper_core import WATCHLIST, daily, calculate


def send(token,chat,message):
    r=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':message},timeout=20)
    r.raise_for_status()
    if not r.json().get('ok'): raise RuntimeError('Telegram rejected message')


def main():
    token=os.getenv('TELEGRAM_BOT_TOKEN','').strip()
    chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
    if not token or not chat: raise RuntimeError('Telegram secrets missing')
    now=datetime.now(ZoneInfo('Asia/Riyadh'))
    # Runs after close; don't send results based on a prior trading day's candles.
    today=now.date().isoformat()
    results=[]; failed=[]
    for ticker in WATCHLIST:
        try:
            d=daily(ticker)
            if d is None:
                failed.append(ticker);continue
            val=calculate(ticker,d)
            if val:results.append(val)
            else:failed.append(ticker)
        except Exception:
            failed.append(ticker)
    if not results: raise RuntimeError('No usable stock data; aborted without notification')
    latest=max(x['التاريخ'] for x in results)
    if latest != today:
        print(f'No notification: latest candle {latest}, today {today}.')
        return
    # No forced recommendations: only validated strategy and backtest sample.
    opportunities=sorted((r for r in results if r['الحالة']=='فرصة مشروطة' and r['التاريخ']==today),
                         key=lambda r:r['درجة_الجودة'],reverse=True)[:5]
    if not opportunities:
        print(f'No validated opportunities for {today}; no Telegram notification.')
        return
    lines=[f'🇸🇦 AI Sniper V5 — {today}', '⚠️ إشارات مشروطة مبنية على إغلاق يومي، ليست أسعارًا لحظية.']
    for r in opportunities:
        lines.append(f"\n📈 {r['السهم']} | تقييم {r['درجة_الجودة']}/100\n"
                     f"دخول بعد التأكيد: {r['بداية_نطاق_الشراء']} ر.س\n"
                     f"وقف: {r['وقف_تقديري']} | هدف 1: {r['هدف_تقديري']} | هدف 2: {r['هدف_ثان']}\n"
                     f"الاستراتيجية: {r['الاستراتيجيات']} | عائد/مخاطرة: {r['نسبة_العائد_للمخاطرة']}")
    lines.append('\nراجع السعر المباشر وسيولة الجلسة قبل التنفيذ. لا يوجد ضمان للربح.')
    send(token,chat,'\n'.join(lines))
    print(f'Sent {len(opportunities)} qualified setups')

if __name__=='__main__':main()
