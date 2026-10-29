
"""AI Sniper V5 - Telegram alerts for validated TASI setups."""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from market_scanner import run


def strategy_label(names):
    names = set((names or "").split(","))

    if "crash_hunter" in names:
        return "🎯 CRASH HUNTER | صائد الهبوط"
    if "breakout" in names:
        return "⚡ مضاربة سريعة | اختراق"
    if "support_rebound" in names:
        return "⚡ مضاربة سريعة | ارتداد دعم"

    return "📊 إشارة فنية"


def send_telegram(token, chat_id, message):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram rejected message")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        raise RuntimeError("Telegram secrets missing")

    report = run(max_candidates=50, quote_budget=0)

    today = datetime.now(
        ZoneInfo("Asia/Riyadh")
    ).date().isoformat()

    candidates = report["historical_candidates"]

    qualified = [
        item for item in candidates
        if item.get("تصنيف_الماسح") == "فرصة مشروطة"
        and item.get("التاريخ") == today
    ]

    qualified = qualified[:5]

    print("Market:", report["universe_size"])
    print("Watchlist:", len(candidates))
    print("Qualified today:", len(qualified))

    if not qualified:
        print("No validated setups. No Telegram alert.")
        return

    for item in qualified:
        label = strategy_label(
            item.get("الاستراتيجيات")
        )

        message = (
            f"{label}\n\n"
            f"🇸🇦 AI SNIPER V5\n"
            f"السهم: {item['اسم_الشركة']}\n"
            f"الرمز: {item['السهم']}\n"
            f"التقييم الفني: {item['درجة_الجودة']}/100\n\n"
            f"🟢 دخول مشروط: "
            f"{item['بداية_نطاق_الشراء']} ريال\n"
            f"🔴 وقف تقديري: "
            f"{item['وقف_تقديري']} ريال\n"
            f"🎯 الهدف الأول: "
            f"{item['هدف_تقديري']} ريال\n"
            f"🎯 الهدف الثاني: "
            f"{item['هدف_ثان']} ريال\n\n"
            f"تاريخ الشمعة: {item['التاريخ']}\n"
            f"الحالة: فرصة مشروطة\n\n"
            f"⚠️ بيانات يومية تاريخية وليست لحظية.\n"
            f"راجع السعر والسيولة قبل أي أمر شراء."
        )

        send_telegram(token, chat_id, message)

    print("Telegram messages sent:", len(qualified))


if __name__ == "__main__":
    main()
