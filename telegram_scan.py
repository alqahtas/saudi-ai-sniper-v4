"""GitHub Actions: scan daily historical Tadawul candles and send digest after close.
Not real time; no trading orders; errors raise a non-zero exit code.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from sniper_core import WATCHLIST, daily, calculate

def send(token, chat, message):
    response = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id":chat, "text":message}, timeout=20)
    response.raise_for_status()
    payload=response.json()
    if not payload.get('ok'):
        raise RuntimeError('Telegram rejected the message')

def main():
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    if not token or not chat:
        raise RuntimeError('Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID under GitHub Actions secrets')
    results=[];errors=[]
    for t in WATCHLIST:
        try:
            d=daily(t)
            if d is None:
                errors.append(t)
                continue
            val=calculate(t,d)
            if val: results.append(val)
        except Exception:
            errors.append(t)
    if not results:
        raise RuntimeError('No market data available; no notification sent')
    date=max(r['التاريخ'] for r in results)
    today=datetime.now(ZoneInfo('Asia/Riyadh')).date().isoformat()
    signals=[r for r in results if r['الحالة']=='فرصة مشروطة']
    watching=[r for r in results if r['الحالة']=='مراقبة دعم']
    lines=["🇸🇦 Saudi AI Sniper V4 — التقرير اليومي", f"آخر شمعة متوفرة: {date}", f"اليوم: {today}", "⚠️ تحليل شموع يومية تاريخية، وليس سعرًا لحظيًا أو توصية شراء.", f"الفرص المشروطة: {len(signals)} | مراقبة دعم: {len(watching)} | فشل جلب: {len(errors)}"]
    if signals:
        for r in signals[:8]:
            lines.append(f"\n📊 {r['السهم']}: دخول مشروط {r['بداية_نطاق_الشراء']}–{r['نهاية_نطاق_الشراء']} ريال | وقف {r['وقف_تقديري']} | هدف {r['هدف_تقديري']} | عائد/مخاطرة {r['نسبة_العائد_للمخاطرة']}")
        lines.append("راجع شاشة الوسيط والبيانات الحية قبل أي أمر.")
    else:
        lines.append('لا توجد فرص دخول تحقق كل شروط V4 في البيانات المتوفرة.')
    if date < today:
        lines.append('⚠️ البيانات ليست من جلسة اليوم، تحقق من حداثتها.')
    send(token,chat,'\n'.join(lines))
    print('Daily report delivered')

if __name__ == '__main__':
    main()
