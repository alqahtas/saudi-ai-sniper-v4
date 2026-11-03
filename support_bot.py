"""Public Saudi stock analysis bot.

One polling instance only. Public channel alerts are sent by the alert workers;
users query this bot privately with a TASI symbol or Arabic/English company name.
"""
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from fundamental_engine import analyze as fundamental_analysis
from market_scanner import BASE_URL, sahmk_session
from sahmk_intelligence import fetch_intelligence, format_pro_sections
from sniper_core import daily
from stock_advisor import build_recommendation, format_recommendation
from telegram_delivery import send_message

LOG = logging.getLogger("saudi-public-bot")
TZ = ZoneInfo("Asia/Riyadh")
DIRECTORY_TTL = 6 * 60 * 60
RATE_LIMIT = max(1, int(os.getenv("RATE_LIMIT_PER_MIN", "10")))
DIRECTORY = {"loaded_at": 0.0, "rows": []}
USER_REQUESTS = defaultdict(deque)


def _normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for source, target in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        text = text.replace(source, target)
    return re.sub(r"[^0-9a-z\u0600-\u06ff]+", "", text)


def _company_rows(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("results", "data", "companies", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _company_rows(value)
            if found:
                return found
    return []


def company_directory(session=None, now=None):
    now = time.time() if now is None else float(now)
    if DIRECTORY["rows"] and now - DIRECTORY["loaded_at"] < DIRECTORY_TTL:
        return DIRECTORY["rows"]
    session = session or sahmk_session()
    rows, offset = [], 0
    for _ in range(10):
        response = session.get(
            f"{BASE_URL}/companies/",
            params={"market": "TASI", "limit": 500, "offset": offset},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        batch = _company_rows(payload)
        for item in batch:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().replace(".SR", "")
            if not symbol.isdigit():
                continue
            market = str(item.get("market") or "TASI").upper()
            status = str(item.get("status") or "active").lower()
            if market != "TASI" or status not in ("active", ""):
                continue
            rows.append({
                "symbol": symbol,
                "name_ar": str(item.get("name_ar") or item.get("name") or symbol).strip(),
                "name_en": str(item.get("name_en") or "").strip(),
                "short_name": str(item.get("short_name") or item.get("ticker") or "").strip(),
            })
        offset += len(batch)
        total = payload.get("total") if isinstance(payload, dict) else None
        if not batch or len(batch) < 500 or (isinstance(total, int) and offset >= total):
            break
    if not rows:
        raise RuntimeError("SAHMK company directory returned no TASI equities")
    DIRECTORY.update({"loaded_at": now, "rows": rows})
    return rows


def resolve_company(query, rows):
    query = str(query or "").strip()
    symbol = query.upper().replace(".SR", "")
    if symbol.isdigit():
        matches = [row for row in rows if row["symbol"] == symbol]
        return matches[:1]

    needle = _normalize(query)
    if not needle:
        return []
    aliases = {
        "aramco": "2222", "ارامكو": "2222",
        "alrajhi": "1120", "الراجحي": "1120",
        "stc": "7010", "الاتصالات": "7010",
        "sabic": "2010", "سابك": "2010",
    }
    alias_symbol = aliases.get(needle)
    if alias_symbol:
        matches = [row for row in rows if row["symbol"] == alias_symbol]
        if matches:
            return matches

    exact, partial = [], []
    for row in rows:
        names = [_normalize(row.get(key)) for key in ("name_ar", "name_en", "short_name")]
        names = [name for name in names if name]
        if needle in names:
            exact.append(row)
        elif any(needle in name or name in needle for name in names):
            partial.append(row)
    return (exact or partial)[:5]


def _market_open(now=None):
    now = now or datetime.now(TZ)
    return now.weekday() in (6, 0, 1, 2, 3) and (10, 0) <= (now.hour, now.minute) < (15, 0)


def completed_daily(candles, now=None):
    df = candles.sort_index().copy()
    now = now or datetime.now(TZ)
    if _market_open(now) and len(df):
        last_date = getattr(df.index[-1], "date", lambda: None)()
        if last_date == now.date():
            df = df.iloc[:-1]
    return df


def _allowed(user_id, now=None):
    now = time.time() if now is None else float(now)
    queue = USER_REQUESTS[str(user_id)]
    while queue and now - queue[0] >= 60:
        queue.popleft()
    if len(queue) >= RATE_LIMIT:
        return False
    queue.append(now)
    return True


def analyze_company(company):
    symbol = company["symbol"]
    name = company.get("name_ar") or company.get("name_en") or symbol
    quote, fundamentals = {}, {}
    try:
        session = sahmk_session()
        response = session.get(f"{BASE_URL}/quote/{symbol}/", params={"data_mode": "realtime"}, timeout=20)
        response.raise_for_status()
        payload = response.json()
        quote = payload if isinstance(payload, dict) else {}
    except Exception as exc:
        LOG.warning("quote_failed symbol=%s error=%s", symbol, type(exc).__name__)

    candles = completed_daily(daily(symbol))
    try:
        fundamentals = fundamental_analysis(symbol)
    except Exception as exc:
        LOG.warning("fundamentals_failed symbol=%s error=%s", symbol, type(exc).__name__)

    plan = build_recommendation(candles, quote, fundamentals)
    message = format_recommendation(symbol, name, plan, quote, fundamentals)
    if quote.get("is_delayed") is True:
        message = "⏱️ تنبيه: السعر مزوّد كبيانات متأخرة.\n" + message

    try:
        intelligence = fetch_intelligence(symbol)
        message += format_pro_sections(intelligence)
    except Exception as exc:
        LOG.warning("pro_intelligence_failed symbol=%s error=%s", symbol, type(exc).__name__)
        message += "\n\n📊 بيانات SAHMK Pro: تعذر جلبها مؤقتًا، والتحليل الأساسي أعلاه ما زال صالحًا."
    return message



def levels(symbol):
    """Backward-compatible symbol lookup used by older deployments/tests."""
    symbol = str(symbol or "").strip().upper().replace(".SR", "")
    if not symbol.isdigit():
        return "رمز السهم غير صالح."
    matches = resolve_company(symbol, company_directory())
    if not matches:
        return "لم يتم العثور على السهم."
    return analyze_company(matches[0])

def _help():
    return (
        "أرسل اسم الشركة أو رمزها، مثل:\n"
        "أرامكو\n2222\nالراجحي\nstc\n\n"
        "سأعطيك سعرًا، دعومًا ومقاومات، زخمًا، وضعًا ماليًا، "
        "وأمر LIMIT مقترحًا مع وقف 2% وهدفين وتصنيف مدة الصفقة، "
        "مع عمق السوق والتنفيذات وفريم 60 دقيقة والبيانات المالية الموسعة."
    )


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    api = f"https://api.telegram.org/bot{token}"
    offset = None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        response = requests.post(api + "/deleteWebhook", json={"drop_pending_updates": False}, timeout=20)
        response.raise_for_status()
        LOG.info("Webhook removed; polling mode enabled")
    except Exception as exc:
        LOG.warning("delete_webhook_failed error=%s", type(exc).__name__)

    LOG.info("Public Saudi analysis command listener started")
    while True:
        try:
            response = requests.get(
                api + "/getUpdates",
                params={"timeout": 25, "offset": offset, "allowed_updates": '["message"]'},
                timeout=35,
            )
            if response.status_code == 409:
                LOG.critical("Telegram 409: another polling instance is running")
                time.sleep(30)
                continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError("Telegram getUpdates failed")
            for update in payload.get("result", []):
                offset = max(offset or 0, int(update["update_id"]) + 1)
                msg = update.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id") or "")
                user = msg.get("from") or {}
                user_id = str(user.get("id") or chat_id)
                raw = str(msg.get("text") or "").strip()
                command = raw.split(maxsplit=1)[0].split("@", 1)[0].lower() if raw else ""
                LOG.info("command_received user_id=%s command=%s", user_id, command if command.startswith("/") else "stock_query")
                if not chat_id or not raw:
                    continue

                if command in ("/start", "/help"):
                    answer = _help()
                elif command == "/status":
                    answer = "✅ البوت يعمل. أرسل اسم شركة أو رمز سهم سعودي."
                elif not _allowed(user_id):
                    answer = "وصلت الحد المؤقت: 10 تحليلات في الدقيقة. جرّب بعد دقيقة."
                else:
                    try:
                        matches = resolve_company(raw, company_directory())
                        if not matches:
                            answer = "ما لقيت الشركة. جرّب الاسم الرسمي أو رمز تداول المكوّن من 4 أرقام."
                        elif len(matches) > 1:
                            options = "\n".join(
                                f"{item['name_ar']} ({item['symbol']})" for item in matches
                            )
                            answer = "الاسم يحتمل أكثر من شركة. أرسل الرمز المطلوب:\n" + options
                        else:
                            answer = analyze_company(matches[0])
                    except Exception as exc:
                        LOG.exception("analysis_failed error=%s", type(exc).__name__)
                        answer = "تعذر إكمال التحليل الآن. البيانات ناقصة أو مزود السوق غير متاح؛ جرّب لاحقًا."
                send_message(token, chat_id, answer)
        except Exception as exc:
            LOG.error("polling_failed error=%s", type(exc).__name__)
            time.sleep(15)


if __name__ == "__main__":
    main()
