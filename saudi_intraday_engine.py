"""Read-only Saudi intraday opportunity engine backed only by SAHMK Pro.

The module deliberately has no scheduler, Telegram sender, or order methods.
It scans once when called and returns conditional setups based on completed
15-minute candles, live quotes, top-5 depth, and recent aggressor flow.
"""
from dataclasses import asdict, dataclass, field
from chart_indicators import analyze as chart_analysis, frame_rows
from datetime import datetime, timedelta, timezone
import math

import pandas as pd

from market_scanner import sahmk_session, tasi_equities
from sahmk_intelligence import SahmkProClient, depth_metrics, trade_flow_metrics
from sahmk_pro import bulk_quotes
from timeframe_analysis import intraday_frame
from v9_indicators import advanced_features


MAX_QUOTE_AGE_SECONDS = 180
DEEP_SCAN_LIMIT = 20


@dataclass(frozen=True)
class SaudiIntradaySetup:
    symbol: str
    company: str
    signal: str
    price: float
    quote_time: str
    entry_low: float
    entry_high: float
    invalidation: float
    support: float
    resistance: float
    next_resistance: float | None
    score: int
    evidence: tuple[str, ...]
    chart_indicators: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _number(value):
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _find(payload, names):
    if not isinstance(payload, dict):
        return None
    for name in names:
        if payload.get(name) is not None:
            return payload[name]
    for key in ("data", "quote", "result", "stock"):
        nested = payload.get(key)
        found = _find(nested, names) if isinstance(nested, dict) else None
        if found is not None:
            return found
    return None


def _parse_time(value):
    if value in (None, ""):
        return None
    try:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("Asia/Riyadh")
        return stamp.tz_convert("UTC").to_pydatetime()
    except (TypeError, ValueError):
        return None


def verified_quote(payload, now=None, max_age=MAX_QUOTE_AGE_SECONDS):
    """Return a normalized quote only when SAHMK explicitly verifies live data."""
    if not isinstance(payload, dict) or _find(payload, ("is_delayed",)) is not False:
        return None
    price = _number(_find(payload, ("price", "last", "last_price", "close")))
    stamp = _parse_time(_find(payload, ("updated_at", "timestamp", "last_update", "time")))
    if price is None or price <= 0 or stamp is None:
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = (now.astimezone(timezone.utc) - stamp).total_seconds()
    if age < -60 or age > max_age:
        return None
    volume = _number(_find(payload, ("volume", "total_volume", "traded_volume"))) or 0.0
    turnover = _number(_find(payload, ("turnover", "value", "traded_value")))
    return {
        "price": price,
        "volume": volume,
        "turnover": turnover if turnover is not None else price * volume,
        "timestamp": stamp.isoformat(),
        "age_seconds": age,
    }


def completed_candles(frame, now=None, interval_minutes=15):
    """Remove the open candle using its timestamp, not a blind last-row drop."""
    data = frame.copy() if isinstance(frame, pd.DataFrame) else intraday_frame(frame)
    if data.empty or not isinstance(data.index, pd.DatetimeIndex):
        return data.iloc[0:0]
    now_stamp = pd.Timestamp(now or datetime.now(timezone.utc))
    if now_stamp.tzinfo is None:
        now_stamp = now_stamp.tz_localize("UTC")
    index = data.index
    if index.tz is None:
        index = index.tz_localize("Asia/Riyadh")
    index = index.tz_convert("UTC")
    closed = index + pd.Timedelta(minutes=interval_minutes) <= now_stamp.tz_convert("UTC")
    result = data.loc[closed].copy()
    result.index = index[closed]
    return result


def _market_regime(payload):
    change = _number(_find(payload, ("index_change_percent", "change_percent", "change_pct")))
    advancing = _number(_find(payload, ("advancing", "gainers")))
    declining = _number(_find(payload, ("declining", "losers")))
    if change is None or advancing is None or declining is None:
        return "unknown"
    if change < -0.6 and declining > advancing * 1.4:
        return "negative"
    if change > 0.3 and advancing > declining * 1.2:
        return "positive"
    return "mixed"


def _session_levels(data):
    local = data.copy()
    local.index = local.index.tz_convert("Asia/Riyadh")
    sessions = local.groupby(local.index.date)
    days = list(sessions)
    if not days:
        return None, None
    prior = days[-2][1] if len(days) > 1 else days[-1][1]
    return float(prior["Low"].min()), float(prior["High"].max())


def evaluate_setup(symbol, company, candles, quote_payload, depth_payload,
                   trades_payload, market_payload, now=None):
    """Evaluate one symbol. A price rise alone can never produce a setup."""
    quote = verified_quote(quote_payload, now=now)
    if quote is None:
        return None
    data = completed_candles(candles, now=now, interval_minutes=15)
    if len(data) < 55:
        return None
    data = advanced_features(data)
    row = data.iloc[-1]
    prior = data.iloc[:-1].tail(20)
    required = ("ema9", "ema20", "rsi14", "atr14", "macd_hist", "rel_volume_v9")
    if any(pd.isna(row[name]) for name in required):
        return None

    depth = depth_metrics(depth_payload)
    flow = trade_flow_metrics(trades_payload)
    imbalance = depth.get("imbalance")
    buy_ratio = flow.get("buy_ratio")
    spread_pct = depth.get("spread_pct")
    if imbalance is None or buy_ratio is None or spread_pct is None or spread_pct > 0.35:
        return None

    close = float(row["Close"])
    open_price = float(row["Open"])
    low = float(row["Low"])
    high = float(row["High"])
    atr = float(row["atr14"])
    rel_volume = float(row["rel_volume_v9"])
    resistance = float(prior["High"].max())
    support = float(prior["Low"].min())
    daily_support, daily_resistance = _session_levels(data)
    regime = _market_regime(market_payload)

    breakout = (
        close > resistance
        and float(row["ema9"]) > float(row["ema20"])
        and float(row["macd_hist"]) > 0
        and 48 <= float(row["rsi14"]) <= 78
        and rel_volume >= 1.5
        and imbalance >= 1.0
        and buy_ratio >= 0.55
        and regime != "negative"
    )
    rebound = (
        low <= support * 1.003
        and close > support
        and close > open_price
        and float(row["rsi14"]) <= 58
        and rel_volume >= 1.1
        and imbalance >= 1.1
        and buy_ratio >= 0.55
        and regime != "negative"
    )
    if not breakout and not rebound:
        return None

    trigger = resistance if breakout else support
    # Reject a valid historical pattern if the live quote has already run away.
    max_chase = max(atr * 0.50, trigger * 0.006)
    if quote["price"] > trigger + max_chase:
        return None

    if breakout:
        entry_low, entry_high = resistance, resistance + min(atr * 0.20, resistance * 0.002)
        invalidation = min(float(row["Low"]), resistance - atr * 0.35)
        signal = "اختراق مؤكد وإعادة اختبار مشروطة"
    else:
        entry_low, entry_high = support, support + min(atr * 0.25, support * 0.003)
        invalidation = support - atr * 0.35
        signal = "ارتداد دعم مؤكد مشروط"

    evidence = (
        f"إغلاق 15د مكتمل {close:.2f}",
        f"حجم نسبي {rel_volume:.2f}x",
        f"عمق طلب/عرض {imbalance:.2f}",
        f"تنفيذات شراء {buy_ratio * 100:.1f}%",
        f"تاسي {regime}",
    )
    score = 70 + min(10, int((rel_volume - 1) * 8))
    score += 5 if imbalance >= 1.25 else 0
    score += 5 if buy_ratio >= 0.62 else 0
    score += 5 if regime == "positive" else 0
    next_resistance = daily_resistance if daily_resistance and daily_resistance > entry_high else None
    effective_support = min(support, daily_support) if daily_support else support
    return SaudiIntradaySetup(
        symbol=str(symbol), company=str(company), signal=signal,
        price=round(quote["price"], 2), quote_time=quote["timestamp"],
        entry_low=round(entry_low, 2), entry_high=round(entry_high, 2),
        invalidation=round(invalidation, 2), support=round(effective_support, 2),
        resistance=round(resistance, 2),
        next_resistance=round(next_resistance, 2) if next_resistance else None,
        score=min(score, 95), evidence=evidence,
        chart_indicators=chart_analysis(frame_rows(data)),
    )


def _quote_rows(payloads):
    rows = []
    for payload in payloads:
        if isinstance(payload, list):
            rows.extend(item for item in payload if isinstance(item, dict))
        elif isinstance(payload, dict):
            for key in ("data", "results", "quotes", "stocks", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows.extend(item for item in value if isinstance(item, dict))
                    break
    return rows


def scan_once(deep_limit=DEEP_SCAN_LIMIT, now=None):
    """Scan TASI once; callers decide scheduling and notification policy."""
    session = sahmk_session()
    universe = tasi_equities(session)
    quotes = bulk_quotes(universe.keys())
    ranked = []
    for payload in _quote_rows(quotes):
        symbol = str(_find(payload, ("symbol", "ticker", "code")) or "")
        normalized = verified_quote(payload, now=now)
        if symbol in universe and normalized:
            ranked.append((normalized["turnover"], symbol, payload))
    ranked.sort(reverse=True)

    client = SahmkProClient(session=session)
    market = client.market()
    setups, errors = [], {}
    for _, symbol, quote in ranked[:max(1, min(int(deep_limit), DEEP_SCAN_LIMIT))]:
        try:
            setup = evaluate_setup(
                symbol, universe[symbol], client.intraday(symbol, "15min"), quote,
                client.depth(symbol, 5), client.trades(symbol, 100), market, now=now,
            )
            if setup:
                setups.append(setup.to_dict())
        except Exception as exc:
            errors[symbol] = type(exc).__name__
    setups.sort(key=lambda item: item["score"], reverse=True)
    return {
        "source": "SAHMK Pro",
        "market": "TASI",
        "universe_size": len(universe),
        "deep_scanned": min(len(ranked), max(1, min(int(deep_limit), DEEP_SCAN_LIMIT))),
        "setups": setups,
        "errors": errors,
        "read_only": True,
    }

