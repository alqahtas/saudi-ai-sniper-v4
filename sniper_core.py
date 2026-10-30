"""Saudi AI Sniper V5.1 — historical daily analysis bridge.

Read-only. No live prices or broker orders. Weekly analysis lives in
market_scanner.weekly_setup().
"""
import math
from sniper_v5 import analyze, download_daily

WATCHLIST = ('2222 1120 1180 1150 1010 1050 2010 2020 1211 7010 '
             '7020 7030 2082 2083 5110 4001 4002 4003 4004 4005 '
             '4006 4013 4164 4200 4300 7200 7202 7203 7204 8010').split()
CAPITAL_SAR = 50_000
MAX_ENTRY_SAR = 10_000
RISK_PER_TRADE_SAR = 150
STOP_LOSS_PCT = .015  # legacy compatibility; ATR-based stop used below
MIN_AVG_VALUE_SAR = 20_000_000
MIN_REL_VOLUME = 1.10
MIN_RR = 1.5
SUPPORT_WINDOW = 30


def daily(ticker):
    """Load historical daily candles, never mislabel them as live quotes."""
    try:
        data = download_daily(ticker, years=5)
        if data is None or len(data) < 230:
            print(f"Daily history insufficient for {ticker}", flush=True)
            return None
        return data
    except Exception as exc:
        print(f"Daily history unavailable for {ticker}: {type(exc).__name__}: {exc}", flush=True)
        return None


def finite(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError, OverflowError):
        return default


def positive(value):
    value = finite(value)
    return value if value is not None and value > 0 else None


def calculate(ticker, candles):
    """Generate a conditional daily setup using historical analysis."""
    result = analyze(ticker, candles)
    if not isinstance(result, dict) or result.get("error"):
        return None

    price = positive(result.get("close"))
    atr = positive(result.get("atr14"))
    support = positive(result.get("support20"))
    if price is None or atr is None or support is None:
        return None

    # Keep existing entry/stop logic for backward compatibility.
    entry = round(price + 0.1 * atr, 3)
    stop = round(min(support - 0.15 * atr, entry - 1.5 * atr), 3)
    risk = entry - stop
    if stop <= 0 or risk <= 0:
        return None

    rr = max(2.0, MIN_RR)
    target = round(entry + rr * risk, 3)
    second = round(entry + 3 * risk, 3)
    active = [str(x).strip() for x in (result.get("active_strategies") or []) if str(x).strip()]
    backtests = result.get("backtests") or {}
    if not isinstance(backtests, dict):
        backtests = {}
    tested = {key: val for key, val in backtests.items()
              if key in active and isinstance(val, dict)}
    enough = any(
        (finite(val.get("samples"), 0) >= 20)
        and (finite(val.get("mean_return_pct"), 0) > 0)
        for val in tested.values()
    )

    liquidity = finite(result.get("avg_turnover_m_sar"), 0)
    score = finite(result.get("quality_score_100"), 0)
    relative_volume = finite(result.get("relative_volume"), 0)
    liquid = liquidity >= MIN_AVG_VALUE_SAR / 1_000_000
    volume_ok = relative_volume >= MIN_REL_VOLUME

    shares_by_budget = int(MAX_ENTRY_SAR // entry)
    shares_by_risk = int(RISK_PER_TRADE_SAR // risk)
    shares = max(0, min(shares_by_budget, shares_by_risk))
    strong = bool(active) and enough and liquid and volume_ok and score >= 75 and shares > 0

    reasons = []
    if not active:
        reasons.append("لا توجد استراتيجية نشطة")
    if not enough:
        reasons.append("الاختبار التاريخي غير كافٍ")
    if not liquid:
        reasons.append("السيولة أقل من الحد المطلوب")
    if not volume_ok:
        reasons.append("الحجم النسبي أقل من الحد المطلوب")
    if score < 75:
        reasons.append("درجة الجودة أقل من 75")
    if shares <= 0:
        reasons.append("المخاطرة لا تسمح بكمية شراء")

    return {
        "السهم": str(ticker),
        "التاريخ": result.get("date"),
        "الإطار_الزمني": "daily",
        "نوع_البيانات": "شموع يومية تاريخية - ليست أسعارًا لحظية",
        "الإغلاق_المرجعي": price,
        "الدعم": support,
        "المقاومة": finite(result.get("resistance60")),
        "بداية_نطاق_الشراء": entry,
        "نهاية_نطاق_الشراء": entry,
        "وقف_تقديري": stop,
        "هدف_تقديري": target,
        "هدف_ثان": second,
        "قيمة_التداول_اليومية_المتوسطة_مليون": liquidity,
        "الحجم_النسبي": relative_volume,
        "RSI14": finite(result.get("rsi14")),
        "EMA20": None,
        "EMA50": None,
        "نسبة_العائد_للمخاطرة": rr,
        "كمية_افتراضية": shares,
        "إجمالي_الشراء_ريال": round(shares * entry, 2),
        "الحالة": "فرصة مشروطة" if strong else "لا دخول",
        "درجة_الجودة": score,
        "الاستراتيجيات": ",".join(active),
        "اختبارات_تاريخية": {
            key: {field: val.get(field) for field in
                  ("samples", "win_rate_pct", "mean_return_pct")}
            for key, val in tested.items()
        },
        "السبب_عند_الرفض": "" if strong else "، ".join(reasons),
    }
