"""V5 bridge: keep existing Streamlit imports and telegram scanner compatible.
Historical daily data, no live prices, no automatic orders.
"""
from datetime import datetime
import numpy as np
from sniper_v5 import analyze, download_daily

WATCHLIST = '2222 1120 1180 1150 1010 1050 2010 2020 1211 7010 7020 7030 2082 2083 5110 4001 4002 4003 4004 4005 4006 4013 4164 4200 4300 7200 7202 7203 7204 8010'.split()
CAPITAL_SAR=50_000
MAX_ENTRY_SAR=10_000
RISK_PER_TRADE_SAR=150
STOP_LOSS_PCT=.015
MIN_AVG_VALUE_SAR=20_000_000
MIN_REL_VOLUME=1.10
MIN_RR=1.5
SUPPORT_WINDOW=30


def daily(ticker):
    try:
        d=download_daily(ticker,years=5)
        if d is None or len(d)<230: return None
        return d
    except Exception:
        return None


def calculate(ticker,d):
    a=analyze(ticker,d)
    if a.get('error'): return None
    price=float(a['close']); atr=float(a['atr14']); support=float(a['support20'])
    active=a['active_strategies']
    # Conservative setup: forward entry trigger above reference close, NOT a current executable quote.
    entry=round(price+0.1*atr,3)
    stop=round(min(support-0.15*atr, entry-1.5*atr),3)
    risk=entry-stop
    rr=2.0
    target=round(entry+rr*risk,3)
    second=round(entry+3*risk,3)
    # 15-bar historical outcome backtests are *not* predictive win probabilities.
    tested={k:v for k,v in a['backtests'].items() if k in active}
    enough=any(v['samples']>=20 and v['mean_return_pct'] is not None and v['mean_return_pct']>0 for v in tested.values())
    liquid=a['avg_turnover_m_sar']>=20
    strong=bool(active) and enough and liquid and a['quality_score_100']>=75 and stop>0 and risk>0
    shares=min(int(MAX_ENTRY_SAR//entry),int(RISK_PER_TRADE_SAR//risk)) if risk>0 else 0
    strong = strong and shares>0
    return {
        'السهم':ticker,'التاريخ':a['date'],'الإغلاق_المرجعي':price,
        'الدعم':support,'المقاومة':a['resistance60'],
        'بداية_نطاق_الشراء':entry,'نهاية_نطاق_الشراء':entry,
        'وقف_تقديري':stop,'هدف_تقديري':target,'هدف_ثان':second,
        'قيمة_التداول_اليومية_المتوسطة_مليون':a['avg_turnover_m_sar'],
        'الحجم_النسبي':a['relative_volume'],'RSI14':a['rsi14'],
        'EMA20':None,'EMA50':None,'نسبة_العائد_للمخاطرة':rr,
        'كمية_افتراضية':shares,'إجمالي_الشراء_ريال':round(shares*entry,2),
        'الحالة':'فرصة مشروطة' if strong else 'لا دخول',
        'درجة_الجودة':a['quality_score_100'],'الاستراتيجيات':','.join(active),
        'اختبارات_تاريخية':{k:{q:v[q] for q in ('samples','win_rate_pct','mean_return_pct')}
                            for k,v in tested.items()},
        'السبب_عند_الرفض':'لا يوجد تأكيد كافٍ أو أقل من 20 حالة إيجابية في الاختبار التاريخي' if not strong else ''
    }
