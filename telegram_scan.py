"""Saudi AI Sniper V5 — TASI-aware Telegram alerts (read-only).

Supports one-shot runs and an optional scheduled polling loop.
Polling historical candles does NOT make the data real-time.
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from market_scanner import run
from sahmk_pro import tasi_market

TZ = ZoneInfo("Asia/Riyadh")
STATE_FILE = Path(os.getenv("ALERT_STATE_FILE", "alert_state.json"))
INTERVAL = max(60, int(os.getenv("SCAN_INTERVAL_SECONDS", "300")))
CONTINUOUS = os.getenv("RUN_CONTINUOUS", "false").lower() == "true"


def strategy_label(raw):
    names = set(str(raw or "").lower().split(","))
    names = {name.strip() for name in names}
    if "crash_hunter" in names:
        return "🎯 CRASH HUNTER | صائد الهبوط"
    if "breakout" in names:
        return "⚡ مضاربة | اختراق"
    if "support_rebound" in names:
        return "⚡ مضاربة | ارتداد دعم"
    if names.intersection({"weekly", "swing", "investment", "weekly_support"}):
        return "📈 فرصة أسبوعية | متوسط المدى"
    return "📊 إشارة فنية"


def is_weekly(item):
    strategies = {
        part.strip().lower()
        for part in str(item.get("الاستراتيجيات") or "").split(",")
    }
    return bool(
        strategies.intersection(
            {"weekly", "swing", "investment", "weekly_support"}
        )
    ) or str(item.get("الإطار_الزمني", "")).lower() in {
        "1w", "weekly", "أسبوعي"
    }


def market_status():
    data = tasi_market()
    required = ("index_value", "index_change_percent", "advancing", "declining")
    if any(data.get(k) is None for k in required):
        raise RuntimeError("Incomplete TASI data — stop, no signals sent")
    change = float(data["index_change_percent"])
    advancing = int(data["advancing"])
    declining = int(data["declining"])
    if advancing + declining == 0:
        raise RuntimeError("No TASI breadth data — stop, no signals sent")
    if change > 0 and advancing > declining * 1.4:
        status, label = "positive", "🟢 إيجابي"
    elif change < 0 and declining > advancing * 1.4:
        status, label = "negative", "🔴 سلبي"
    else:
        status, label = "mixed", "🟡 متذبذب"
    return {
        "status": status,
        "label": label,
        "index": data["index_value"],
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


def load_sent():
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_sent(sent):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    temp.replace(STATE_FILE)


def parsed_date(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (ValueError, TypeError):
        return None


def session_open(now):
    return (
        now.weekday() in (6, 0, 1, 2, 3)
        and (10, 0) <= (now.hour, now.minute) < (15, 0)
    )


def scan_once():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID")

    now = datetime.now(TZ)
    market = market_status()
    report = run(max_candidates=50, quote_budget=0)
    candidates = report.get("historical_candidates", [])
    if not isinstance(candidates, list):
        raise RuntimeError("Unexpected scanner output")

    sent = load_sent()
    threshold = (now.date() - timedelta(days=45)).isoformat()
    sent = {key: val for key, val in sent.items() if str(val) >= threshold}
    sent_count = 0

    for item in candidates:
        if item.get("تصنيف_الماسح") != "فرصة مشروطة":
            continue

        candle_date = parsed_date(item.get("التاريخ"))
        weekly = is_weekly(item)
        if candle_date is None:
            continue

        age = (now.date() - candle_date).days
        if age < 0 or age > (7 if weekly else 0):
            continue

        if market["status"] != "positive":
            print(
                "Watch only (TASI risk):",
                item.get("السهم"),
                market["label"],
            )
            continue

        symbol = str(item.get("السهم") or "").strip()
        if not symbol:
            continue

        strategies = str(item.get("الاستراتيجيات") or "")
        key = f"{symbol}|{strategies}|{candle_date.isoformat()}"
        if key in sent:
            continue

        message = (
            f"🇸🇦 Saudi AI Sniper V5\n"
            f"{strategy_label(strategies)}\n"
            f"السهم: {item.get('اسم_الشركة', symbol)} ({symbol})\n"
            f"تاسي: {market['label']} | {market['change']:+.2f}%\n"
            f"التقييم الفني: {item.get('درجة_الجودة', 'غير متاح')}/100\n"
            f"منطقة شراء مشروطة: {item.get('بداية_نطاق_الشراء', 'غير متاح')} ريال\n"
            f"وقف تقديري: {item.get('وقف_تقديري', 'غير متاح')} ريال\n"
            f"هدف أول: {item.get('هدف_تقديري', 'غير متاح')} ريال\n"
            f"هدف ثانٍ: {item.get('هدف_ثان', 'غير متاح')} ريال\n"
            f"تاريخ الشمعة: {candle_date.isoformat()}\n"
            f"الإطار: {'أسبوعي' if weekly else 'يومي'}\n\n"
            "⚠️ تنبيه مبني على شموع تاريخية، وليس تأكيدًا لحظيًا للدخول. "
            "تحقق من السعر والسيولة والسبريد مباشرة قبل أي قرار. "
            "لا توجد أوامر شراء أو بيع آلية."
        )
        send_telegram(token, chat_id, message)
        sent[key] = now.date().isoformat()
        save_sent(sent)
        sent_count += 1

        if sent_count >= 5:
            break

    print(f"Scan completed {now.isoformat()}, alerts: {sent_count}")


def main():
    if not CONTINUOUS:
        scan_once()
        return

    print(f"Continuous mode enabled; polling every {INTERVAL} seconds")
    while True:
        now = datetime.now(TZ)
        if session_open(now):
            try:
                scan_once()
            except Exception as exc:
                print(f"Scan failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
