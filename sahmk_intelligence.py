"""SAHMK Pro intelligence layer for the Saudi analysis bot.

Network calls are isolated here. Every optional data block fails independently so
one plan/endpoint problem never removes the core technical recommendation.
"""
import logging
import math
import os
import time
from collections import deque
from datetime import date, timedelta

import requests

from market_scanner import BASE_URL, sahmk_session

LOG = logging.getLogger(__name__)
CACHE = {}
GLOBAL_CALLS = deque()
GLOBAL_RATE_PER_MIN = max(20, min(int(os.getenv("SAHMK_GLOBAL_RATE_PER_MIN", "300")), 450))

TTLS = {
    "quote": 5,
    "depth": 5,
    "trades": 5,
    "intraday": 3600,
    "company": 86400,
    "ratios": 86400,
    "financials": 86400,
    "dividends": 86400,
    "market": 30,
    "events": 900,
}


class PlanLimitError(RuntimeError):
    pass


class SahmkTemporaryError(RuntimeError):
    pass


def _rows(payload, keys=()):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in tuple(keys) + ("data", "results", "items", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _rows(value, keys)
            if found:
                return found
    return []


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(",", "").replace("%", "").strip()
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _find(data, aliases, depth=0):
    if depth > 6:
        return None
    if isinstance(data, dict):
        for key in aliases:
            if key in data:
                value = _number(data[key])
                if value is not None:
                    return value
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = _find(value, aliases, depth + 1)
                if found is not None:
                    return found
    elif isinstance(data, list):
        for value in data:
            found = _find(value, aliases, depth + 1)
            if found is not None:
                return found
    return None


def _text(data, aliases, depth=0):
    if depth > 5:
        return None
    if isinstance(data, dict):
        for key in aliases:
            value = data.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        for value in data.values():
            if isinstance(value, (dict, list)):
                found = _text(value, aliases, depth + 1)
                if found:
                    return found
    elif isinstance(data, list):
        for value in data:
            found = _text(value, aliases, depth + 1)
            if found:
                return found
    return None


def _global_slot(now=None):
    now = time.time() if now is None else float(now)
    while GLOBAL_CALLS and now - GLOBAL_CALLS[0] >= 60:
        GLOBAL_CALLS.popleft()
    if len(GLOBAL_CALLS) >= GLOBAL_RATE_PER_MIN:
        raise SahmkTemporaryError("global client rate guard reached")
    GLOBAL_CALLS.append(now)


class SahmkProClient:
    def __init__(self, session=None, sleep=time.sleep, now=time.time):
        self.session = session or sahmk_session()
        self.sleep = sleep
        self.now = now

    def _get(self, path, params=None, *, cache_key=None, ttl=0):
        key = (cache_key or path, tuple(sorted((params or {}).items())))
        cached = CACHE.get(key)
        current = self.now()
        if cached and current - cached[0] < ttl:
            return cached[1]

        last_error = None
        for attempt in range(3):
            _global_slot(current)
            try:
                response = self.session.get(BASE_URL + path, params=params, timeout=25)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                raise SahmkTemporaryError(type(exc).__name__) from exc

            status = int(response.status_code)
            if status == 403:
                code = ""
                try:
                    body = response.json()
                    code = str(body.get("code") or body.get("error") or "")
                except (ValueError, AttributeError):
                    pass
                raise PlanLimitError(code or "HTTP 403")
            if status == 429 or status >= 500:
                last_error = RuntimeError("HTTP " + str(status))
                if attempt < 2:
                    delay = response.headers.get("Retry-After", 2 ** attempt)
                    try:
                        delay = max(0.0, min(float(delay), 30.0))
                    except (TypeError, ValueError):
                        delay = float(2 ** attempt)
                    self.sleep(delay)
                    continue
                raise SahmkTemporaryError("HTTP " + str(status))
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                raise ValueError("Unexpected SAHMK response")
            if ttl:
                CACHE[key] = (current, payload)
            return payload
        raise SahmkTemporaryError(type(last_error).__name__)

    def quote(self, symbol):
        return self._get(f"/quote/{symbol}/", {"data_mode": "realtime"},
                         cache_key=f"quote:{symbol}", ttl=TTLS["quote"])

    def depth(self, symbol, levels=5):
        levels = max(1, min(int(levels), 5))
        return self._get(f"/market/depth/{symbol}/", {"levels": levels},
                         cache_key=f"depth:{symbol}:{levels}", ttl=TTLS["depth"])

    def trades(self, symbol, limit=100):
        limit = max(1, min(int(limit), 200))
        return self._get(f"/market/trades/{symbol}/", {"limit": limit},
                         cache_key=f"trades:{symbol}:{limit}", ttl=TTLS["trades"])

    def intraday(self, symbol, interval="60min"):
        if interval not in ("15min", "30min", "60min"):
            raise ValueError("Unsupported intraday interval")
        end = date.today()
        # Pro permits up to 90 calendar days. 89 days plus both inclusive
        # endpoints stays inside that limit; a 90-day subtraction spans 91 dates.
        start = end - timedelta(days=89)
        return self._get(
            f"/historical/{symbol}/",
            {"interval": interval, "from": start.isoformat(), "to": end.isoformat(),
             "limit": 500, "offset": 0},
            cache_key=f"intraday:{symbol}:{interval}", ttl=TTLS["intraday"],
        )

    def company(self, symbol):
        return self._get(f"/company/{symbol}/", cache_key=f"company:{symbol}",
                         ttl=TTLS["company"])

    def ratios(self, symbol):
        return self._get(
            f"/analytics/ratios/{symbol}/",
            {"history": "latest", "period": "quarterly", "metrics": "extended",
             "meta": "extended"},
            cache_key=f"ratios:{symbol}", ttl=TTLS["ratios"],
        )

    def financials(self, symbol):
        return self._get(
            f"/financials/{symbol}/",
            {"period": "quarterly", "history": "3y", "metrics": "extended", "limit": 12},
            cache_key=f"financials:{symbol}", ttl=TTLS["financials"],
        )

    def dividends(self, symbol):
        return self._get(f"/dividends/{symbol}/", cache_key=f"dividends:{symbol}",
                         ttl=TTLS["dividends"])

    def market(self):
        return self._get("/market/summary/", {"index": "TASI", "data_mode": "realtime"},
                         cache_key="market:TASI", ttl=TTLS["market"])

    def events(self, symbol):
        return self._get("/events/", {"symbol": symbol, "importance": "IMPORTANT", "limit": 5},
                         cache_key=f"events:{symbol}", ttl=TTLS["events"])


def fetch_intelligence(symbol, client=None):
    """Fetch all Pro blocks independently and return partial results on failure."""
    symbol = str(symbol).strip().replace(".SR", "")
    if not symbol.isdigit():
        raise ValueError("Invalid Saudi symbol")
    client = client or SahmkProClient()
    calls = {
        "quote": lambda: client.quote(symbol),
        "depth": lambda: client.depth(symbol, 5),
        "trades": lambda: client.trades(symbol, 100),
        "intraday": lambda: client.intraday(symbol, "60min"),
        "company": lambda: client.company(symbol),
        "ratios": lambda: client.ratios(symbol),
        "financials": lambda: client.financials(symbol),
        "dividends": lambda: client.dividends(symbol),
        "market": client.market,
        "events": lambda: client.events(symbol),
    }
    result, errors = {}, {}
    for name, call in calls.items():
        try:
            result[name] = call()
        except PlanLimitError:
            errors[name] = "غير مفعّل ضمن الصلاحية الحالية"
            LOG.warning("sahmk_plan_limit block=%s symbol=%s", name, symbol)
        except Exception as exc:
            errors[name] = "غير متوفر مؤقتًا"
            LOG.warning("sahmk_block_failed block=%s symbol=%s error=%s",
                        name, symbol, type(exc).__name__)
    return {"symbol": symbol, "data": result, "errors": errors}


def _level_rows(payload, side):
    source = payload
    if isinstance(source, dict) and isinstance(source.get("data"), dict):
        source = source["data"]
    aliases = (side, "buy" if side == "bids" else "sell",
               "bid_levels" if side == "bids" else "ask_levels")
    values = []
    if isinstance(source, dict):
        for key in aliases:
            if isinstance(source.get(key), list):
                values = source[key]
                break
    parsed = []
    for row in values[:5]:
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            price, quantity = _number(row[0]), _number(row[1])
        elif isinstance(row, dict):
            price = _find(row, ("price", "bid_price", "ask_price"))
            quantity = _find(row, ("quantity", "qty", "volume", "size"))
        else:
            continue
        if price is not None and quantity is not None and price > 0 and quantity >= 0:
            parsed.append((price, quantity))
    return parsed


def depth_metrics(payload):
    bids = _level_rows(payload, "bids")
    asks = _level_rows(payload, "asks")
    bid_value = sum(price * qty for price, qty in bids)
    ask_value = sum(price * qty for price, qty in asks)
    imbalance = bid_value / ask_value if ask_value > 0 else (math.inf if bid_value else None)
    spread = asks[0][0] - bids[0][0] if bids and asks else None
    mid = (asks[0][0] + bids[0][0]) / 2 if bids and asks else None
    spread_pct = spread / mid * 100 if spread is not None and mid else None
    if imbalance is None:
        reading = "عمق السوق غير متوفر."
    elif imbalance >= 1.35:
        reading = "طلبات الشراء أقوى من العروض في أول 5 مستويات."
    elif imbalance <= 0.74:
        reading = "عروض البيع أقوى من الطلبات في أول 5 مستويات."
    else:
        reading = "العمق متوازن نسبيًا في أول 5 مستويات."
    return {
        "bids": bids, "asks": asks, "bid_value": bid_value, "ask_value": ask_value,
        "imbalance": imbalance, "spread": spread, "spread_pct": spread_pct,
        "reading": reading,
    }


def trade_flow_metrics(payload):
    rows = _rows(payload, ("trades",))
    buy_value = sell_value = unknown_value = 0.0
    largest = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        price = _find(row, ("price", "trade_price"))
        quantity = _find(row, ("quantity", "qty", "volume", "size"))
        value = _find(row, ("value", "trade_value", "amount"))
        value = value if value is not None else (
            price * quantity if price is not None and quantity is not None else 0.0
        )
        side = str(row.get("side") or row.get("aggressor_side") or "").lower()
        if side in ("buy", "bid", "b"):
            buy_value += value
        elif side in ("sell", "ask", "s"):
            sell_value += value
        else:
            unknown_value += value
        if value:
            largest.append(value)
    known = buy_value + sell_value
    ratio = buy_value / known if known > 0 else None
    if ratio is None:
        reading = "اتجاه الصفقات المنفذة غير متوفر."
    elif ratio >= 0.60:
        reading = "التنفيذات الأخيرة تميل للشراء."
    elif ratio <= 0.40:
        reading = "التنفيذات الأخيرة تميل للبيع."
    else:
        reading = "التنفيذات الأخيرة متوازنة."
    return {
        "count": len(rows), "buy_value": buy_value, "sell_value": sell_value,
        "unknown_value": unknown_value, "buy_ratio": ratio,
        "largest_value": max(largest) if largest else None, "reading": reading,
    }


def intraday_metrics(payload):
    rows = _rows(payload, ("candles", "historical", "history"))
    candles = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        high = _find(row, ("high", "h"))
        low = _find(row, ("low", "l"))
        close = _find(row, ("close", "c"))
        volume = _find(row, ("volume", "v"))
        stamp = _text(row, ("timestamp", "datetime", "date", "time"))
        if None not in (high, low, close):
            candles.append({"high": high, "low": low, "close": close,
                            "volume": volume or 0.0, "time": stamp})
    if len(candles) < 3:
        return {"count": len(candles), "trend": "غير متوفر",
                "support": None, "resistance": None, "volume_ratio": None}
    # Last record may be the current 60-minute candle; exclude it from levels.
    completed = candles[:-1]
    recent = completed[-20:]
    closes = [x["close"] for x in recent]
    volumes = [x["volume"] for x in recent]
    start, end = closes[0], closes[-1]
    move = (end / start - 1) * 100 if start else 0
    trend = "صاعد" if move > 0.5 else "هابط" if move < -0.5 else "عرضي"
    avg_volume = sum(volumes[:-1]) / max(1, len(volumes) - 1)
    volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else None
    return {
        "count": len(completed), "trend": trend,
        "support": min(x["low"] for x in recent),
        "resistance": max(x["high"] for x in recent),
        "move_pct": move, "volume_ratio": volume_ratio,
    }


def financial_metrics(ratios, financials, dividends):
    return {
        "pe": _find(ratios, ("pe_ratio", "price_to_earnings", "pe", "trailing_pe")),
        "pb": _find(ratios, ("pb_ratio", "price_to_book", "pb")),
        "roe": _find(ratios, ("roe", "return_on_equity", "roe_percent")),
        "net_margin": _find(ratios, ("net_margin", "net_profit_margin", "net_profit_margin_percent")),
        "debt_equity": _find(ratios, ("debt_to_equity", "de_ratio")),
        "revenue_growth": _find(ratios, ("revenue_growth_yoy", "revenue_growth", "sales_growth_yoy")),
        "income_growth": _find(ratios, ("net_income_growth_yoy", "net_income_growth", "profit_growth_yoy")),
        "dividend_yield": _find(dividends, ("dividend_yield_ttm", "ttm_yield", "dividend_yield", "yield")),
        "quarter_count": len(_rows(financials, ("income_statements", "statements"))),
    }


def event_summary(payload):
    rows = _rows(payload, ("events",))
    if not rows:
        return None
    item = rows[0] if isinstance(rows[0], dict) else {}
    title = _text(item, ("title_ar", "title", "name_ar", "name"))
    date_value = _text(item, ("event_date", "published_at", "date", "created_at"))
    return {"title": title, "date": date_value} if title else None


def _fmt(value, digits=2, suffix=""):
    return "غير متوفر" if value is None or not math.isfinite(float(value)) else f"{float(value):,.{digits}f}{suffix}"


def format_pro_sections(bundle):
    data = bundle.get("data") or {}
    errors = bundle.get("errors") or {}
    depth = depth_metrics(data.get("depth") or {})
    flow = trade_flow_metrics(data.get("trades") or {})
    intra = intraday_metrics(data.get("intraday") or {})
    financial = financial_metrics(data.get("ratios") or {}, data.get("financials") or {},
                                  data.get("dividends") or {})
    market = data.get("market") or {}
    event = event_summary(data.get("events") or {})

    lines = ["", "📊 بيانات SAHMK Pro"]
    quote = data.get("quote") or {}
    updated = _text(quote, ("updated_at", "timestamp", "last_update", "time")) or "غير متوفر"
    lines.append("وقت السعر: " + updated + (" | متأخر" if quote.get("is_delayed") is True else " | لحظي"))

    if depth["bids"] or depth["asks"]:
        lines.append("📚 عمق السوق — أفضل 5 مستويات")
        lines.append("طلبات: " + " | ".join(f"{p:.2f}×{q:,.0f}" for p, q in depth["bids"]))
        lines.append("عروض: " + " | ".join(f"{p:.2f}×{q:,.0f}" for p, q in depth["asks"]))
        lines.append("السبريد: " + _fmt(depth["spread"], 2) + " ريال (" +
                     _fmt(depth["spread_pct"], 3, "%") + ")")
        lines.append("نسبة قيمة الطلب/العرض: " + _fmt(depth["imbalance"], 2))
        lines.append(depth["reading"])
    else:
        lines.append("📚 عمق السوق: " + errors.get("depth", "غير متوفر حاليًا"))

    lines.extend([
        "🧾 آخر التنفيذات: " + flow["reading"],
        "شراء: " + _fmt(flow["buy_value"], 0, " ر.س") +
        " | بيع: " + _fmt(flow["sell_value"], 0, " ر.س") +
        " | نسبة الشراء: " + _fmt(None if flow["buy_ratio"] is None else flow["buy_ratio"] * 100, 1, "%"),
        "⏱️ فريم 60 دقيقة: " + intra["trend"] +
        " | دعم " + _fmt(intra.get("support")) +
        " | مقاومة " + _fmt(intra.get("resistance")) +
        " | تغير " + _fmt(intra.get("move_pct"), 2, "%"),
    ])

    index_value = _find(market, ("index_value", "value", "last", "close"))
    index_change = _find(market, ("index_change_percent", "change_percent", "change_pct"))
    lines.append("🇸🇦 تاسي: " + _fmt(index_value) + " | " + _fmt(index_change, 2, "%"))

    lines.extend([
        "🏦 بيانات مالية ربع سنوية/موسعة",
        "P/E " + _fmt(financial["pe"]) + " | P/B " + _fmt(financial["pb"]) +
        " | ROE " + _fmt(financial["roe"], 2, "%"),
        "هامش صافي " + _fmt(financial["net_margin"], 2, "%") +
        " | دين/حقوق " + _fmt(financial["debt_equity"]) +
        " | عائد توزيعات " + _fmt(financial["dividend_yield"], 2, "%"),
        "نمو الإيرادات " + _fmt(financial["revenue_growth"], 2, "%") +
        " | نمو الربح " + _fmt(financial["income_growth"], 2, "%"),
    ])
    if event:
        lines.append("🗓️ حدث مهم: " + event["title"] +
                     ((" — " + event["date"]) if event.get("date") else ""))
    elif "events" in errors:
        lines.append("🗓️ الأحداث: " + errors["events"])
    return "\n".join(lines)
