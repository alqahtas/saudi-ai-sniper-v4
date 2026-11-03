"""Pure Saudi-stock recommendation logic. No network calls are made here."""
import math
import os

import numpy as np
import pandas as pd


def as_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def nested_value(data, aliases, depth=0):
    if depth > 5:
        return None
    if isinstance(data, dict):
        for key in aliases:
            if key in data:
                value = as_number(data[key])
                if value is not None:
                    return value
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = nested_value(value, aliases, depth + 1)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for value in data:
            found = nested_value(value, aliases, depth + 1)
            if found is not None:
                return found
    return None


def confirmed_pivots(candles, left=2, right=2):
    """Confirmed pivots only; the final two candles can never become pivots."""
    highs = list(map(float, candles["High"]))
    lows = list(map(float, candles["Low"]))
    pivot_highs, pivot_lows = [], []
    end = len(candles) - right
    for i in range(left, max(left, end)):
        high_window = highs[i - left:i] + highs[i + 1:i + right + 1]
        low_window = lows[i - left:i] + lows[i + 1:i + right + 1]
        if len(high_window) == left + right and highs[i] > max(high_window):
            pivot_highs.append(highs[i])
        if len(low_window) == left + right and lows[i] < min(low_window):
            pivot_lows.append(lows[i])
    return pivot_lows, pivot_highs


def merge_levels(levels, tolerance=0.005):
    merged = []
    for level in sorted(float(x) for x in levels if as_number(x) and float(x) > 0):
        if not merged or abs(level - merged[-1]) / merged[-1] > tolerance:
            merged.append(level)
        else:
            merged[-1] = (merged[-1] + level) / 2
    return merged


def _round_levels(price):
    if price < 20:
        step = 0.5
    elif price < 100:
        step = 1.0
    else:
        step = 5.0
    center = round(price / step) * step
    return [center + step * offset for offset in (-2, -1, 0, 1, 2)]


def valuation_verdict(metrics):
    """Transparent rules; banks/insurers still require sector-specific review."""
    metrics = metrics or {}
    pe = as_number(metrics.get("price_to_earnings"))
    pb = as_number(metrics.get("price_to_book"))
    roe = as_number(metrics.get("return_on_equity_reported"))
    margin = as_number(metrics.get("net_profit_margin_percent"))
    debt = as_number(metrics.get("debt_to_equity"))
    income = as_number(metrics.get("annual_net_income_reported"))

    if income is not None and income <= 0:
        return "مرتفع المخاطر", "صافي الدخل غير موجب"
    if (pe is not None and pe > 30) or (pb is not None and pb > 5):
        return "مرتفع", "مكرر الربحية فوق 30 أو مكرر الدفترية فوق 5"
    cheap_checks = [
        pe is not None and 0 < pe <= 15,
        pb is not None and 0 < pb <= 2.5,
        roe is not None and roe >= 10,
        margin is not None and margin > 0,
        debt is None or debt <= 1.5,
    ]
    if sum(cheap_checks) >= 4:
        return "جذاب نسبيًا", "تحقق 4 شروط: P/E≤15 وP/B≤2.5 وROE≥10% وربحية موجبة ودين منضبط"
    if all(value is None for value in (pe, pb, roe, margin, debt, income)):
        return "غير متوفر", "البيانات المالية غير كافية"
    return "عادل", "لم تتحقق شروط الجاذبية أو المبالغة كاملة"


def build_recommendation(candles, quote=None, fundamentals=None, *, stop_pct=None):
    if candles is None or len(candles) < 220:
        raise ValueError("At least 220 completed daily candles are required")
    df = candles.sort_index().copy()
    required = ("Open", "High", "Low", "Close", "Volume")
    if any(name not in df.columns for name in required):
        raise ValueError("OHLCV columns are required")
    for name in required:
        df[name] = pd.to_numeric(df[name], errors="coerce")
    df = df.dropna(subset=list(required))
    if len(df) < 220:
        raise ValueError("Insufficient valid completed candles")

    current = nested_value(
        quote or {}, ("price", "last", "last_price", "lastPrice", "close", "lastTradePrice")
    ) or float(df["Close"].iloc[-1])
    if current <= 0:
        raise ValueError("Invalid current price")

    close, high, low, volume = df["Close"], df["High"], df["Low"], df["Volume"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else np.inf
    rsi = 100 - 100 / (1 + rs)
    previous = close.shift(1)
    tr = pd.concat((high - low, (high - previous).abs(), (low - previous).abs()), axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    avg_volume = float(volume.tail(20).mean())
    rel_volume = float(volume.iloc[-1] / avg_volume) if avg_volume > 0 else 0.0
    macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    macd_hist = float((macd - macd.ewm(span=9, adjust=False).mean()).iloc[-1])

    pivot_lows, pivot_highs = confirmed_pivots(df.tail(260))
    extra = [float(low.tail(252).min()), float(high.tail(252).max())] + _round_levels(current)
    all_levels = merge_levels(pivot_lows + pivot_highs + extra)
    supports = sorted((x for x in all_levels if x < current), reverse=True)[:3]
    resistances = sorted(x for x in all_levels if x > current)[:3]
    nearest_support = supports[0] if supports else current * 0.98

    entry = min(current * 0.995, nearest_support * 1.002)
    stop_pct = float(stop_pct if stop_pct is not None else os.getenv("SAUDI_STOP_LOSS_PCT", "0.02"))
    stop_pct = min(max(stop_pct, 0.005), 0.10)
    stop = entry * (1 - stop_pct)
    risk = entry - stop

    target1_floor = entry + 1.5 * risk
    target2_floor = entry + 2.5 * risk
    target1 = next((x for x in resistances if x >= target1_floor), target1_floor)
    target2 = next((x for x in resistances if x >= max(target2_floor, target1 * 1.01)), target2_floor)
    if target2 <= target1:
        target2 = max(target2_floor, target1 + risk)

    metrics = (fundamentals or {}).get("metrics") or {}
    valuation, valuation_rule = valuation_verdict(metrics)
    strong_up = current > ema20 > ema50 > ema200 and macd_hist >= 0
    weak = current < ema50 and ema20 < ema50 and macd_hist < 0
    near_support_pct = abs(current - nearest_support) / current * 100
    atr_pct = atr / current * 100

    if weak or rsi < 30:
        classification = "مراقبة فقط"
        horizon = "لا دخول حتى ظهور ارتداد مؤكد"
        status = "WAIT"
    elif strong_up and valuation not in ("مرتفع", "مرتفع المخاطر"):
        classification = "فرصة أسابيع"
        horizon = "من أسبوعين إلى شهر"
        status = "SETUP"
    elif near_support_pct <= 2.5 and (rel_volume >= 1.15 or atr_pct >= 1.0):
        classification = "مضاربة لحظية"
        horizon = "جلسة إلى 3 جلسات"
        status = "SETUP"
    else:
        classification = "مضاربة قصيرة"
        horizon = "2 إلى 10 جلسات"
        status = "SETUP"

    bias_points = sum((current > ema20, ema20 > ema50, ema50 > ema200, macd_hist > 0, rsi >= 50))
    bias = "إيجابي" if bias_points >= 4 else ("سلبي" if bias_points <= 1 else "محايد")
    return {
        "current": round(current, 2),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_reward1": round((target1 - entry) / risk, 2),
        "risk_reward2": round((target2 - entry) / risk, 2),
        "supports": [round(x, 2) for x in supports],
        "resistances": [round(x, 2) for x in resistances],
        "ema20": round(float(ema20), 2),
        "ema50": round(float(ema50), 2),
        "ema200": round(float(ema200), 2),
        "rsi": round(float(rsi), 1),
        "atr_pct": round(atr_pct, 2),
        "relative_volume": round(rel_volume, 2),
        "macd_hist": round(macd_hist, 4),
        "classification": classification,
        "horizon": horizon,
        "status": status,
        "bias": bias,
        "valuation": valuation,
        "valuation_rule": valuation_rule,
        "financial_metrics": metrics,
    }


def _fmt(value, suffix=""):
    number = as_number(value)
    return "غير متوفر" if number is None else f"{number:.2f}{suffix}"


def format_recommendation(symbol, company_name, plan, quote=None, fundamentals=None):
    quote = quote or {}
    metrics = plan.get("financial_metrics") or {}
    supports = "، ".join(map(str, plan["supports"])) or "غير متوفر"
    resistances = "، ".join(map(str, plan["resistances"])) or "غير متوفر"
    change = nested_value(quote, ("change_percent", "changePercent", "percent_change"))
    bid = nested_value(quote, ("bid", "best_bid", "bestBid"))
    ask = nested_value(quote, ("ask", "best_ask", "bestAsk"))
    day_high = nested_value(quote, ("high", "day_high", "highPrice"))
    day_low = nested_value(quote, ("low", "day_low", "lowPrice"))
    volume = nested_value(quote, ("volume", "total_volume", "totalVolume"))
    action = (
        f"ضع أمر LIMIT مقترح عند {plan['entry']:.2f} ريال"
        if plan["status"] == "SETUP"
        else f"لا تدخل الآن؛ راقب ارتدادًا مؤكدًا قرب {plan['entry']:.2f} ريال"
    )
    return (
        f"🇸🇦 تحليل {company_name} ({symbol})\n"
        f"السعر: {plan['current']:.2f} ريال | التغير: {_fmt(change, '%')}\n"
        f"عرض/طلب: {_fmt(bid)} / {_fmt(ask)} | أعلى/أدنى: {_fmt(day_high)} / {_fmt(day_low)}\n"
        f"الحجم: {_fmt(volume)}\n\n"
        f"🎯 الخطة: {plan['classification']} — {plan['horizon']}\n"
        f"{action}\n"
        f"وقف الخسارة: {plan['stop']:.2f} ريال (-2% من الدخول)\n"
        f"الهدف الأول: {plan['target1']:.2f} ريال | عائد/مخاطرة {plan['risk_reward1']:.2f}\n"
        f"الهدف الثاني: {plan['target2']:.2f} ريال | عائد/مخاطرة {plan['risk_reward2']:.2f}\n"
        f"الدعوم: {supports}\nالمقاومات: {resistances}\n\n"
        f"📈 الزخم: {plan['bias']} | RSI {plan['rsi']:.1f} | ATR {plan['atr_pct']:.2f}%\n"
        f"EMA20/50/200: {plan['ema20']:.2f} / {plan['ema50']:.2f} / {plan['ema200']:.2f}\n"
        f"الحجم مقابل متوسط 20 جلسة: {plan['relative_volume']:.2f}x\n\n"
        f"💼 ماليًا: {plan['valuation']}\n"
        f"P/E: {_fmt(metrics.get('price_to_earnings'))} | P/B: {_fmt(metrics.get('price_to_book'))}\n"
        f"ROE: {_fmt(metrics.get('return_on_equity_reported'), '%')} | هامش صافي: {_fmt(metrics.get('net_profit_margin_percent'), '%')}\n"
        f"قاعدة التقييم: {plan['valuation_rule']}\n\n"
        "الخطة مشروطة بثبات الدعم وظهور ارتداد؛ يُلغى السيناريو عند ضرب الوقف.\n"
        "تحليل آلي لأغراض معلوماتية وليس توصية استثمارية"
    )
