"""Private, read-only Telegram support/resistance lookup using SAHMK daily history.

Run separately: python support_bot.py
Do not run multiple Telegram getUpdates pollers for the same token.
"""
import logging
import os
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import requests
from sniper_core import daily

LOG = logging.getLogger("support-bot")
TZ = ZoneInfo("Asia/Riyadh")
SYMBOL = re.compile(r"^(?:/sr(?:@\\w+)?\\s+|/levels(?:@\\w+)?\\s+)?([0-9]{4,5})(?:\\.SR)?$", re.I)


def levels(symbol):
    candles = daily(symbol)
    if candles is None or len(candles) < 60:
        return "لا تتوفر شموع يومية كافية لتحليل هذا السهم."
    # Exclude the last candle from lookback to avoid using its high/low as a prior level.
    df = candles.sort_index()
    close = float(df["Close"].iloc[-1])
    prior = df.iloc[:-1]
    if not np.isfinite(close) or close <= 0:
        return "السعر التاريخي غير صالح."
    low = prior["Low"]
    high = prior["High"]
    supports = sorted(set(round(float(low.tail(n).min()), 2) for n in (10, 30, 60)))
    resistances = sorted(set(round(float(high.tail(n).max()), 2) for n in (10, 30, 60)))
    supports = sorted((x for x in supports if x <= close), reverse=True)
    resistances = sorted(x for x in resistances if x >= close)
    last_day = df.index[-1].strftime("%Y-%m-%d")
    def fmt(values):
        return "، ".join(f"{v:.2f}" for v in values) if values else "لا يوجد مستوى مؤكد ضمن النوافذ"
    return (f"📊 {symbol} | دعوم ومقاومات تاريخية\\n"
            f"آخر إغلاق: {close:.2f} ريال ({last_day})\\n"
            f"الدعوم: {fmt(supports)}\\n"
            f"المقاومات: {fmt(resistances)}\\n"
            "المنهج: أدنى/أعلى الأسعار خلال 10 و30 و60 جلسة سابقة.\\n"
            "⚠️ مستويات فنية تقريبية وليست أسعارًا لحظية أو توصية شراء.")


def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"].strip()
    allowed = str(os.environ["TELEGRAM_CHAT_ID"]).strip()
    api = f"https://api.telegram.org/bot{token}"
    offset = None
    logging.basicConfig(level=logging.INFO)
    LOG.info("Private support/resistance command listener started")
    while True:
        try:
            response = requests.get(api + "/getUpdates", params={
                "timeout": 25, "offset": offset, "allowed_updates": '["message"]'
            }, timeout=35)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError("Telegram getUpdates failed")
            for update in payload.get("result", []):
                offset = max(offset or 0, int(update["update_id"]) + 1)
                msg = update.get("message") or {}
                chat = msg.get("chat") or {}
                if str(chat.get("id")) != allowed:
                    continue
                raw = str(msg.get("text") or "").strip()
                match = SYMBOL.fullmatch(raw)
                if not match:
                    if raw.startswith("/start") or raw.startswith("/help"):
                        answer = "أرسل رمز السهم مثل 1120 أو /sr 1120 لمعرفة الدعوم والمقاومات التاريخية."
                    else:
                        continue
                else:
                    try:
                        answer = levels(match.group(1))
                    except Exception as exc:
                        LOG.exception("Support lookup failed")
                        answer = "تعذر جلب بيانات السهم الآن؛ جرّب لاحقًا."
                sent = requests.post(api + "/sendMessage", json={
                    "chat_id": allowed, "text": answer
                }, timeout=20)
                sent.raise_for_status()
        except Exception:
            LOG.exception("Telegram polling failed")
            time.sleep(15)


if __name__ == "__main__":
    main()
