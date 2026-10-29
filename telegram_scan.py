
"""Saudi AI Sniper V5 - TASI-aware Telegram alerts.
Read-only. No automatic trading orders.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from market_scanner import run
from sahmk_pro import tasi_market


def strategy_label(names):
    names = set((names or "").split(","))

    if "crash_hunter" in names:
        return "🎯 CRASH HUNTER | صائد الهبوط"
    if "breakout" in names:
        return "⚡ مضاربة سريعة | اختراق"
    if "support_rebound" in names:
        return "⚡ مضاربة سريعة | ارتداد دعم"
    return "📊 إشارة فنية"


def market_status():
    """Read TASI data and classify market risk."""
    data = tasi_market()

    value = data.get("index_value")
    change = data.get("index_change_percent")
    advancing = data.get("advancing")
    declining = data.get("declining")

    if any(x is None for x in
           (value, change, advancing, declining)):
        raise RuntimeError("Incomplete TASI data")

    change = float(change)
    advancing = int(advancing)
    declining = int(declining)

    if advancing + declining == 0:
        raise RuntimeError("No market breadth data")

    if change > 0 and advancing > declining * 1.4:
        status = "positive"
        label = "🟢 إيجابي"
    elif change < 0 and declining > advancing * 1.4:
        status = "negative"
        label = "🔴 سلبي"
    else:
        status = "mixed"
        label = "🟡 متذبذب"

    return {
        "status": status,
        "label": label,
        "index": value,
        "change": change,
        "advancing": advancing,
        "declining": declining,
        "updated_at": data.get("updated_at"),
        "is_delayed": data.get("is_delayed"),
    }


def send_telegram(token, chat_id, message):
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=30,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram rejected message")


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        raise RuntimeError("Telegram secrets missing")

    now = datetime.now(ZoneInfo("Asia/Riyadh"))
    today = now.date().isoformat()

    market = market_status()

    print("TASI:", market["index"])
    print("TASI change:", market["change"])
    print("TASI status:", market["label"])
    print("Advancing:", market["advancing"])
    print("Declining:", market["declining"])

    report = run(max_candidates=50, quote_budget=0)

    candidates = report["historical_candidates"]

    qualified = [
        item for item in candidates
        if item.get("تصنيف_الماسح") == "فرصة مشروطة"
        and item.get("التاريخ") == today
    ]

    approved = []

    for item in qualified:
        strategies = set(
            (item.get("الاستراتيجيات") or "").split(",")
        )

        if market["status"] == "positive":
            approved.append(item)

        elif market["status"] == "mixed":
            print(
                "Watch only - mixed market:",
                item["السهم"]
            )

        elif "crash_hunter" in strategies:
            print(
                "Watch only - bearish market:",
                item["السهم"]
            )

        else:
            print(
                "Filtered by bearish TASI:",
                item["السهم"]
            )

    approved = approved[:5]

    print("Qualified before market filter:", len(qualified))
    print("Approved after market filter:", len(approved))

    if not approved:
        print("No approved entry setups. No Telegram alert.")
        return

    lines = [
        f"🇸🇦 AI SNIPER V5 | {today}",
        f"حالة تاسي: {market['label']}",
        f"المؤشر: {market['index']}",
        f"التغير: {market['change']:+.2f}%",
        f"صاعدة: {market['advancing']} | "
        f"هابطة: {market['declining']}",
    ]

    for item in approved:
        lines.append(
            "\n"
            f"{strategy_label(item.get('الاستراتيجيات'))}\n"
            f"السهم: {item['اسم_الشركة']} "
            f"({item['السهم']})\n"
            f"التقييم الفني: {item['درجة_الجودة']}/100\n"
            f"🟢 دخول مشروط: "
            f"{item['بداية_نطاق_الشراء']} ريال\n"
            f"🔴 وقف تقديري: "
            f"{item['وقف_تقديري']} ريال\n"
            f"🎯 الهدف الأول: "
            f"{item['هدف_تقديري']} ريال\n"
            f"🎯 الهدف الثاني: "
            f"{item['هدف_ثان']} ريال\n"
            f"تاريخ الشمعة: {item['التاريخ']}"
        )

    lines.append(
        "\n⚠️ الإشارات من شموع يومية تاريخية. "
        "راجع السعر المباشر والسيولة قبل التنفيذ."
    )

    send_telegram(token, chat_id, "\n\n".join(lines))
    print("Telegram report sent:", len(approved))


if __name__ == "__main__":
    main()
