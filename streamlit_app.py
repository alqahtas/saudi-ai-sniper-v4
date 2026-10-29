"""Saudi AI Sniper V4 — Tadawul manual-entry scanner.
Educational research, no orders, no broker authentication, no background alerts.
Historical daily data from Yahoo Finance via yfinance: not exchange-grade real-time.
"""
from datetime import datetime
from pathlib import Path
import numpy as np
import pandas as pd

# Candidate watchlist only: NOT a verified current top-30 by capitalization or liquidity.
WATCHLIST = '2222 1120 1180 1150 1010 1050 2010 2020 1211 7010 7020 7030 2082 2083 5110 4001 4002 4003 4004 4005 4006 4013 4164 4200 4300 7200 7202 7203 7204 8010'.split()
CAPITAL_SAR = 50_000
MAX_ENTRY_SAR = 10_000
RISK_PER_TRADE_SAR = 150  # 0.3% of earmarked capital; cap, not a guarantee
STOP_LOSS_PCT = .015
MIN_AVG_VALUE_SAR = 20_000_000  # historical 20d value volume proxy, NOT order-book liquidity
MIN_REL_VOLUME = 1.10
MIN_RR = 1.5
SUPPORT_WINDOW = 30


def daily(ticker):
    import yfinance as yf
    d = yf.download(ticker + '.SR', period='9mo', interval='1d', auto_adjust=False,
                    progress=False, threads=False, timeout=12, multi_level_index=False)
    if d is None or len(d)<75: return None
    cols=['Open','High','Low','Close','Volume']
    if not set(cols).issubset(d.columns): return None
    d=d[cols].apply(pd.to_numeric,errors='coerce').dropna()
    return d if len(d)>=75 else None


def calculate(ticker,d):
    last=d.iloc[-1]; prev=d.iloc[:-1]
    price=float(last['Close']); op=float(last['Open']); hi=float(last['High']); low=float(last['Low'])
    if min(price,op,hi,low)<=0: return None
    support=float(prev['Low'].tail(SUPPORT_WINDOW).min())
    resistance=float(prev['High'].tail(40).max())
    volavg=float(prev['Volume'].tail(20).mean())
    relvol=float(last['Volume']/volavg) if volavg>0 else 0
    avgvalue=float((prev['Close']*prev['Volume']).tail(20).mean())
    ema20=float(d['Close'].ewm(span=20,adjust=False).mean().iloc[-1]); ema50=float(d['Close'].ewm(span=50,adjust=False).mean().iloc[-1])
    delta=d['Close'].diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=float(gain.iloc[-1]/max(loss.iloc[-1],1e-10)); rsi=100-100/(1+rs)
    candle_range=max(hi-low,0.00001); lowerwick=(min(op,price)-low)/candle_range; closepos=(price-low)/candle_range
    # Support test must touch support zone and close ABOVE it; avoid presumed rebounds based on last price alone.
    touched=low<=support*1.012 and low>=support*0.975
    confirmation=(price>op and closepos>=.65 and lowerwick>=.20)
    trend=(price>=ema50*.97 and ema20>=ema50*.97)
    liquid=(avgvalue>=MIN_AVG_VALUE_SAR)
    volgood=(relvol>=MIN_REL_VOLUME)
    # The requested manual limit BUY range is ALWAYS below the historical reference close.
    upper=min(price*.995, support*1.012)
    lower=support*1.002
    valid_range=(upper>lower>0 and upper<price)
    entry=(lower+upper)/2 if valid_range else np.nan
    stop=entry*(1-STOP_LOSS_PCT) if valid_range else np.nan
    # Stop should be below the identified historical support, not through the middle of it.
    valid_stop=valid_range and stop<support and (entry-support)/entry<STOP_LOSS_PCT
    potential_target=resistance
    rr=(potential_target-entry)/(entry-stop) if valid_range and entry>stop else 0
    # Fair conservative sizing: execution and stop gap can exceed expected loss.
    shares=min(int(MAX_ENTRY_SAR//entry),int(RISK_PER_TRADE_SAR//(entry-stop))) if valid_range else 0
    conditions={
      'سيولة تاريخية': liquid,'ارتداد قرب دعم': touched,'شمعة تأكيد': confirmation,
      'تدفق حجم نسبي': volgood,'اتجاه فني':trend,'نطاق دون الإغلاق':valid_range,
      'وقف أسفل الدعم':valid_stop,'عائد/مخاطرة':rr>=MIN_RR,'حجم صفقة ممكن':shares>=1
    }
    status='فرصة مشروطة' if all(conditions.values()) else ('مراقبة دعم' if liquid and touched and valid_range else 'لا دخول')
    reason='، '.join(k for k,v in conditions.items() if not v)
    return dict(السهم=ticker,التاريخ=str(d.index[-1].date()),الإغلاق_المرجعي=round(price,3),الدعم=round(support,3),المقاومة=round(resistance,3),
       بداية_نطاق_الشراء=round(lower,3) if valid_range else None,نهاية_نطاق_الشراء=round(upper,3) if valid_range else None,
       وقف_تقديري=round(stop,3) if valid_range else None,هدف_تقديري=round(potential_target,3),
       قيمة_التداول_اليومية_المتوسطة_مليون=round(avgvalue/1e6,1),الحجم_النسبي=round(relvol,2),RSI14=round(rsi,1),
       EMA20=round(ema20,3),EMA50=round(ema50,3),نسبة_العائد_للمخاطرة=round(rr,2),كمية_افتراضية=shares,
       إجمالي_الشراء_ريال=round(shares*entry,2) if shares else 0,الحالة=status,السبب_عند_الرفض=reason or 'شروط تاريخية متحققة؛ التأكيد اللحظي مطلوب')



import streamlit as st
from datetime import timezone

st.set_page_config(page_title="Saudi AI Sniper V4", page_icon="📈", layout="wide")
st.title("🇸🇦 Saudi AI Sniper V4")
st.caption("ماسح تاسي اليدوي — بيانات يومية تاريخية وليست أسعارًا مباشرة. لا ينفّذ صفقات أو يرسل تنبيهات تلقائية.")
st.warning("نتائج تعليمية للمراجعة اليدوية. تحقق من الأسعار والأخبار وسيولة السوق في منصة الوسيط قبل اتخاذ قرار. الوقف التقديري لا يضمن الحد من الخسارة.")
with st.sidebar:
    st.header("إعدادات V4")
    st.metric("رأس مال الاستراتيجية (ريال)", f"{CAPITAL_SAR:,}")
    st.metric("حد الصفقة (ريال)", f"{MAX_ENTRY_SAR:,}")
    st.write("الأسهم المرصودة:", len(WATCHLIST))
    st.write("الدعم: آخر 30 جلسة، الهدف: أعلى 40 جلسة سابقة")

@st.cache_data(ttl=900, show_spinner=False)
def get_data(ticker):
    return daily(ticker)

def execute_scan():
    results=[]; errors=[]
    progress=st.progress(0, text="جاري فحص الأسهم...")
    for idx,t in enumerate(WATCHLIST,1):
        try:
            d=get_data(t)
            if d is None:
                errors.append(f"{t}: بيانات غير كافية")
            else:
                r=calculate(t,d)
                if r is not None: results.append(r)
        except Exception as exc:
            errors.append(f"{t}: {str(exc)[:100]}")
        progress.progress(idx/len(WATCHLIST), text=f"تم فحص {idx}/{len(WATCHLIST)}")
    progress.empty()
    return results,errors

if st.button("🔎 فحص الأسهم الآن", type="primary", use_container_width=True):
    with st.spinner("نجلب الشموع اليومية ونحسب الشروط الفنية..."):
        records, errors=execute_scan()
    if not records:
        st.error("لم يتم الحصول على بيانات كافية. تأكد من اتصال الخدمة أو حاول لاحقًا.")
        if errors:st.write(errors[:12])
    else:
        df=pd.DataFrame(records)
        df['ترتيب']=df['الحالة'].map({'فرصة مشروطة':0,'مراقبة دعم':1,'لا دخول':2})
        df=df.sort_values(['ترتيب','قيمة_التداول_اليومية_المتوسطة_مليون'],ascending=[True,False]).drop(columns='ترتيب').reset_index(drop=True)
        st.session_state['results']=df
        st.session_state['errors']=errors

if 'results' in st.session_state:
    df=st.session_state['results'];errors=st.session_state['errors']
    st.info(f"آخر جلسة متوفرة: {df['التاريخ'].max()} | تاريخ العرض: {datetime.now().strftime('%Y-%m-%d %H:%M')} (توقيت خادم التطبيق)")
    a,b,c=st.columns(3)
    a.metric("فرص مشروطة",int((df['الحالة']=='فرصة مشروطة').sum()))
    b.metric("مراقبة دعم",int((df['الحالة']=='مراقبة دعم').sum()))
    c.metric("أخطاء جلب",len(errors))
    st.subheader("الفرص والمراقبة")
    desired=['السهم','الحالة','الإغلاق_المرجعي','الدعم','بداية_نطاق_الشراء','نهاية_نطاق_الشراء','وقف_تقديري','هدف_تقديري','نسبة_العائد_للمخاطرة','الحجم_النسبي','كمية_افتراضية','السبب_عند_الرفض']
    filtered=df[df['الحالة']!='لا دخول']
    if filtered.empty:st.write("لا توجد فرص مشروطة أو أسهم مراقبة وفق شروط V4 الحالية.")
    else:st.dataframe(filtered[desired],use_container_width=True,hide_index=True)
    st.subheader("نتائج جميع الأسهم")
    st.dataframe(df,use_container_width=True,hide_index=True)
    st.download_button("📥 تنزيل النتائج CSV",data=df.to_csv(index=False).encode('utf-8-sig'),file_name='Saudi_AI_Sniper_V4_Results.csv',mime='text/csv')
    if errors:
        with st.expander("أخطاء البيانات"):
            st.write(errors)
else:
    st.info("اضغط «فحص الأسهم الآن» لتبدأ. يستغرق الفحص وقتًا حسب استجابة مصدر الأسعار.")

st.caption("نسخة واجهة تعتمد منطق V4 الأصلي المرفوع. لا تتصل بدراية، ولا تقرأ دفتر الأوامر، ولا تراقب السوق في الخلفية.")


st.divider()
st.subheader("🔔 اختبار ربط تيليجرام")
st.caption("أرسل /start إلى البوت الجديد أولاً. هذا اختبار إرسال فقط؛ المراقبة المجدولة لها إعداد منفصل في GitHub Actions.")
try:
    _token = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
except (FileNotFoundError, KeyError):
    _token = ""
if not _token:
    st.error("لم يتم العثور على TELEGRAM_BOT_TOKEN في Streamlit Secrets.")
else:
    _pin = st.secrets.get("TELEGRAM_SETUP_PIN", "")
    _entered = st.text_input("رمز حماية إعدادات تيليجرام", type="password")
    if not _pin or _entered != _pin:
        st.info("للحماية: أضف TELEGRAM_SETUP_PIN إلى Secrets، ثم أدخل الرمز هنا لفتح أدوات الربط.")
        st.stop()
    st.success("✅ مفتاح تيليجرام محفوظ (لن نعرضه).")
    import requests
    if st.button("1️⃣ اكتشاف رقم المحادثة Chat ID"):
        try:
            j = requests.get(f"https://api.telegram.org/bot{_token}/getUpdates", timeout=12).json()
            chats = {str(c["message"]["chat"]["id"]): c["message"]["chat"].get("first_name", "")
                     for c in j.get("result", []) if "message" in c and c["message"].get("chat", {}).get("type") == "private"}
            if not j.get("ok", False):
                st.error("تعذّر الاتصال بالبوت. تحقق من المفتاح الجديد في Secrets.")
            elif not chats:
                st.warning("ما ظهر رقم محادثة. افتح البوت الجديد وأرسل /start، ثم أعد المحاولة. إذا كان هناك Webhook مفعل فلن تعمل getUpdates.")
            else:
                st.session_state["tg_chat"] = list(chats)[-1]
                st.info("رقم المحادثة المكتشف: " + st.session_state["tg_chat"] + " — تأكد أنه محادثتك قبل الإرسال. لا ترسل الرقم في الشات.")
        except Exception as e:
            st.error("تعذر الحصول على Chat ID: " + str(e)[:150])
    _chat = st.text_input("2️⃣ Chat ID الخاص بك", value=st.session_state.get("tg_chat", ""), help="يمكن نسخ الرقم من نتيجة الاكتشاف أعلاه.")
    if st.button("3️⃣ أرسل رسالة اختبار إلى تيليجرام"):
        if not _chat.strip().lstrip('-').isdigit():
            st.warning("اكتب Chat ID بالأرقام أولًا.")
        else:
            try:
                r = requests.post(f"https://api.telegram.org/bot{_token}/sendMessage", json={"chat_id": _chat.strip(), "text": "✅ Saudi AI Sniper V4 — تم ربط تيليجرام بنجاح. هذه رسالة اختبار فقط، وليست إشارة شراء."}, timeout=15)
                if r.ok and r.json().get("ok"):
                    st.success("تم إرسال رسالة الاختبار. لتفعيل الفحص المجدول أضف TELEGRAM_BOT_TOKEN وTELEGRAM_CHAT_ID إلى GitHub Actions secrets.")
                else:
                    st.error("فشل إرسال الرسالة. تأكد أنك ضغطت Start في البوت الجديد وأن Chat ID صحيح.")
            except Exception as e:
                st.error("مشكلة اتصال: " + str(e)[:150])
