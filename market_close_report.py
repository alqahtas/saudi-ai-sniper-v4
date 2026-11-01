"""Daily post-close Saudi market bulletin (read-only)."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from sahmk_pro import tasi_market
from telegram_scan import send_telegram

TZ = ZoneInfo("Asia/Riyadh")

def bulletin(data, today):
    if not isinstance(data, dict):
        raise ValueError("Missing TASI summary")
    required = ("index_value", "index_change_percent", "advancing", "declining")
    if any(data.get(k) is None for k in required):
        raise ValueError("Incomplete TASI summary")
    value = float(data["index_value"])
    change = float(data["index_change_percent"])
    adv, dec = int(data["advancing"]), int(data["declining"])
    if adv + dec <= 0:
        raise ValueError("Invalid market breadth")
    updated = str(data.get("updated_at") or "")
    if updated and updated[:10] != today.isoformat():
        raise ValueError("Stale TASI data: " + updated[:10])
    tone = "🟢 إيجابي" if change > 0 and adv > dec * 1.4 else ("🔴 سلبي" if change < 0 and dec > adv * 1.4 else "🟡 مختلط")
    lines = [
        f"🇸🇦 نشرة إغلاق السوق السعودي | {today.isoformat()}",
        f"📊 تاسي: {value:,.2f} نقطة",
        f"التغير: {change:+.2f}%",
        f"حالة السوق: {tone}",
        f"📈 الأسهم الصاعدة: {adv} | 📉 الهابطة: {dec}",
        "اتساع السوق: " + ("الصاعدة أكثر" if adv > dec else "الهابطة أكثر" if dec > adv else "متعادل"),
    ]
    for key, label, unit in (("total_value", "قيمة التداول", "ريال"), ("trading_value", "قيمة التداول", "ريال"), ("total_volume", "حجم التداول", "سهم")):
        if data.get(key) is not None and not any(label in s for s in lines):
            lines.append(f"{label}: {float(data[key]):,.0f} {unit}")
    lines.extend([f"آخر تحديث: {updated or 'غير متاح'}", "⚠️ البيانات متأخرة بحسب المزود" if data.get("is_delayed") else "المصدر: سهمك Pro", "ملخص معلوماتي وليس توصية تداول."])
    return "\n".join(lines)

def main():
    now = datetime.now(TZ)
    if now.weekday() not in (6, 0, 1, 2, 3) or (now.hour, now.minute) < (15, 10):
        print("Outside Saudi post-close window")
        return
    message = bulletin(tasi_market(), now.date())
    send_telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], message)
    print("Closing bulletin sent")

if __name__ == "__main__":
    main()
