
"""Saudi AI Sniper V5.3 — activity-filtered historical TASI scanner.
Read-only; no orders or live-entry confirmation.
"""
import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://api.sahmk.sa/api/v1"
TZ = ZoneInfo("Asia/Riyadh")
LOG = logging.getLogger(__name__)
MIN_LIQUIDITY_M_SAR = 20.0
UNIVERSE_LIMIT = max(10, min(100, int(os.getenv("SCAN_UNIVERSE_LIMIT", "100"))))


def fix_text(value):
    if not isinstance(value, str) or not any(x in value for x in ("Ø", "Ù")):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def normalize_row(row):
    if not isinstance(row, dict):
        return row
    return {fix_text(k): fix_text(v) for k, v in row.items()}


def sahmk_session():
    key = os.getenv("SAHMK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SAHMK_API_KEY is missing")
    session = requests.Session()
    session.headers.update({"X-API-Key": key})
    return session


def tasi_equities(session=None):
    session = session or sahmk_session()
    symbols = {}
    offset = 0
    for _ in range(10):
        response = session.get(
            f"{BASE_URL}/companies/",
            params={"market": "TASI", "limit": 500, "offset": offset},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("Invalid SAHMK companies response")
        for company in rows:
            if not isinstance(company, dict):
                continue
            symbol = str(company.get("symbol", "")).strip()
            if (
                company.get("market") == "TASI"
                and str(company.get("status", "")).lower() == "active"
                and str(company.get("security_type", "")).lower() == "equity"
                and company.get("is_etf") is not True
                and symbol.isdigit()
            ):
                symbols[symbol] = company.get("name_ar") or company.get("name_en") or symbol
        offset += len(rows)
        total = payload.get("total")
        if not rows or (isinstance(total, int) and offset >= total) or len(rows) < 500:
            break
    else:
        raise RuntimeError("Companies directory exceeded 10 pages")
    if not symbols:
        raise RuntimeError("No active TASI equities found")
    return symbols


def select_active_equities(symbols, session=None, limit=UNIVERSE_LIMIT):
    """Select liquid/activity leaders, not a historical top-100 ranking.

    SAHMK leader endpoints each allow up to 50 names. Combine value leaders
    with volume leaders; avoid silently substituting arbitrary directory order.
    """
    session = session or sahmk_session()
    selected = {}
    for endpoint in ("value", "volume"):
        try:
            response = session.get(
                f"{BASE_URL}/market/{endpoint}/",
                params={"index": "TASI", "limit": 50},
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("stocks") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise ValueError("Invalid market leaders response")
            for item in rows:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol", "")).strip()
                if symbol in symbols:
                    selected[symbol] = symbols[symbol]
                if len(selected) >= limit:
                    break
            LOG.info("ACTIVITY %s: received=%d selected=%d", endpoint, len(rows), len(selected))
        except (requests.RequestException, ValueError) as exc:
            LOG.warning("ACTIVITY %s unavailable: %s", endpoint, exc)
        if len(selected) >= limit:
            break
    if not selected:
        raise RuntimeError("No verified activity leaders available; refusing arbitrary stock selection")
    if len(selected) < limit:
        LOG.warning("Only %d unique active symbols found (target=%d)", len(selected), limit)
    return dict(list(selected.items())[:limit])


def normalized_daily(candles):
    import pandas as pd
    if candles is None or candles.empty:
        return None
    df = candles.copy()
    if isinstance(df.columns, pd.MultiIndex):
        return None
    if not isinstance(df.index, pd.DatetimeIndex):
        date_col = next((c for c in df.columns if str(c).lower() in ("date", "datetime", "timestamp")), None)
        if date_col is None:
            return None
        df.index = pd.to_datetime(df[date_col], errors="coerce", utc=True).dt.tz_localize(None)
    elif df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    names = {str(c).lower(): c for c in df.columns}
    required = ("open", "high", "low", "close", "volume")
    if not all(name in names for name in required):
        return None
    df = df[[names[name] for name in required]].copy()
    df.columns = list(required)
    for name in required:
        df[name] = pd.to_numeric(df[name], errors="coerce")
    df = df.dropna().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df if not df.empty else None


def weekly_setup(symbol, company, daily_candles, liquidity):
    import pandas as pd
    df = normalized_daily(daily_candles)
    if df is None or len(df) < 160 or liquidity < MIN_LIQUIDITY_M_SAR:
        return None
    weekly = df.resample("W-THU", label="right", closed="right").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()
    now = datetime.now(TZ)
    today = pd.Timestamp(now.date())
    weekly = weekly.loc[weekly.index <= today]
    if now.weekday() == 3 and (now.hour, now.minute) < (15, 0):
        weekly = weekly.loc[weekly.index < today]
    if len(weekly) < 30:
        return None
    close, volume = weekly["close"], weekly["volume"]
    ma10, ma20 = close.rolling(10).mean(), close.rolling(20).mean()
    vol10 = volume.shift(1).rolling(10).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    support = weekly["low"].shift(1).rolling(12).min()
    latest = weekly.iloc[-1]
    price, base = float(latest["close"]), float(support.iloc[-1])
    if price <= 0 or base <= 0 or pd.isna(vol10.iloc[-1]):
        return None
    conditions = (
        price > float(ma10.iloc[-1]) > float(ma20.iloc[-1]),
        float(ma10.iloc[-1]) > float(ma10.iloc[-2]),
        50 <= float(rsi.iloc[-1]) <= 70,
        base <= price <= base * 1.08,
        float(latest["volume"]) >= float(vol10.iloc[-1]) * 1.20,
    )
    if not all(conditions):
        return None
    stop = round(base * .98, 2)
    risk = price - stop
    if risk <= 0:
        return None
    return {
        "السهم": symbol, "اسم_الشركة": company,
        "الإطار_الزمني": "weekly", "الاستراتيجيات": "weekly_support",
        "الحالة": "فرصة مشروطة", "تصنيف_الماسح": "فرصة مشروطة",
        "درجة_الجودة": 80, "بداية_نطاق_الشراء": round(price, 2),
        "وقف_تقديري": stop, "هدف_تقديري": round(price + 1.5 * risk, 2),
        "هدف_ثان": round(price + 2.5 * risk, 2),
        "التاريخ": weekly.index[-1].date().isoformat(),
        "قيمة_التداول_اليومية_المتوسطة_مليون": liquidity,
        "مستوفي_شرط_السيولة": True,
        "سبب_الإشارة": "اتجاه أسبوعي صاعد + RSI + قرب دعم + حجم أسبوعي مرتفع",
        "نوع_البيانات": "شموع أسبوعية مكتملة، ليست أسعارًا لحظية",
    }


def scan_universe(symbols, max_candidates=50):
    from sniper_core import daily, calculate
    stats = Counter()
    failures, scored, weeklies = [], [], []
    total = len(symbols)
    LOG.info("SCAN START: %d selected TASI equities", total)
    for index, (symbol, company) in enumerate(symbols.items(), 1):
        try:
            candles = daily(symbol)
            if candles is None or candles.empty:
                stats["missing_history"] += 1
                failures.append(symbol)
                continue
            result = calculate(symbol, candles)
            if not isinstance(result, dict):
                stats["analysis_failed"] += 1
                failures.append(symbol)
                continue
            stats["analyzed"] += 1
            item = normalize_row(dict(result))
            item["اسم_الشركة"] = company
            score = float(item.get("درجة_الجودة") or 0)
            liquidity = float(item.get("قيمة_التداول_اليومية_المتوسطة_مليون") or 0)
            liquid = liquidity >= MIN_LIQUIDITY_M_SAR
            stats["liquid" if liquid else "below_liquidity_threshold"] += 1
            validated = liquid and item.get("الحالة") == "فرصة مشروطة"
            if validated:
                stats["validated_setups"] += 1
            item["تصنيف_الماسح"] = "فرصة مشروطة" if validated else "مراقبة فقط"
            item["مستوفي_شرط_السيولة"] = liquid
            item.setdefault("الإطار_الزمني", "daily")
            scored.append((validated, liquid, score, liquidity, item))
            try:
                weekly = weekly_setup(symbol, company, candles, liquidity)
                if weekly:
                    weeklies.append(weekly)
                    stats["weekly_setups"] += 1
            except Exception as exc:
                stats["weekly_errors"] += 1
                LOG.warning("Weekly analysis %s: %s: %s", symbol, type(exc).__name__, exc)
        except Exception as exc:
            stats["exceptions"] += 1
            failures.append(symbol)
            LOG.warning("Stock %s: %s: %s", symbol, type(exc).__name__, str(exc)[:150])
        finally:
            if index == 1 or index % 10 == 0 or index == total:
                LOG.info("SCAN PROGRESS: %d/%d analyzed=%d failures=%d", index, total, stats["analyzed"], len(failures))
    scored.sort(key=lambda row: row[:4], reverse=True)
    top = [row[4] for row in scored[:max(0, int(max_candidates))]]
    weeklies.sort(key=lambda item: item["درجة_الجودة"], reverse=True)
    stats.update(
        universe_size=total, failed_total=len(failures),
        watchlist_returned=len(top),
        watchlist_liquid=sum(bool(item["مستوفي_شرط_السيولة"]) for item in top),
    )
    return top, weeklies, failures, dict(stats)


def rank_universe(symbols, max_candidates=50):
    ranked, _, failed, _ = scan_universe(symbols, max_candidates)
    return ranked, failed


def verify_quotes(candidates, session=None, budget=0):
    budget = max(0, min(int(budget), 20))
    if budget == 0:
        return {}
    session = session or sahmk_session()
    quotes = {}
    for item in candidates[:budget]:
        symbol = str(item["السهم"])
        try:
            response = session.get(f"{BASE_URL}/quote/{symbol}/", timeout=20)
            if response.status_code in (401, 403, 429):
                LOG.warning("SAHMK quote HTTP %s", response.status_code)
                break
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("symbol")) != symbol:
                continue
            quotes[symbol] = {key: payload.get(key) for key in ("price", "volume", "updated_at", "is_delayed")}
        except requests.RequestException as exc:
            LOG.warning("Quote %s: %s", symbol, type(exc).__name__)
    return quotes


def run(max_candidates=50, quote_budget=0):
    LOG.info("Historical scanner requested")
    session = sahmk_session()
    all_symbols = tasi_equities(session)
    symbols = select_active_equities(all_symbols, session, UNIVERSE_LIMIT)
    LOG.info("SAHMK directory=%d selected=%d", len(all_symbols), len(symbols))
    ranked, weeklies, failed, stats = scan_universe(symbols, max_candidates)
    quotes = verify_quotes(ranked, session, budget=quote_budget)
    report = {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": "TASI", "universe_size": len(symbols),
        "directory_size": len(all_symbols),
        "selection_method": "current trading-value leaders + trading-volume leaders (not historical top 100)",
        "summary": stats,
        "historical_candidates": ranked + weeklies,
        "weekly_candidates": weeklies,
        "historical_failures": failed,
        "sahmk_quotes": quotes,
        "note": "Historical daily and completed weekly candles; no live entry confirmation.",
    }
    Path("market_scan_result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    lines = [
        "SAUDI AI SNIPER V5.3 - HISTORICAL SCAN",
        f"Directory: {len(all_symbols)}", f"Selected: {len(symbols)}",
        f"Analyzed: {stats.get('analyzed', 0)}", f"Errors: {len(failed)}",
        f"Daily setups: {stats.get('validated_setups', 0)}",
        f"Weekly setups: {len(weeklies)}", f"Quotes retrieved: {len(quotes)}",
        "TOP 10 DAILY WATCHLIST:",
    ]
    for item in ranked[:10]:
        lines.append(f"{item.get('السهم')} | score={item.get('درجة_الجودة')} | {item.get('تصنيف_الماسح')}")
    lines.append("WEEKLY SETUPS:")
    for item in weeklies[:10]:
        lines.append(f"{item['السهم']} | weekly close={item['بداية_نطاق_الشراء']}")
    Path("market_scan_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOG.info("SCAN COMPLETE: selected=%d analyzed=%d failed=%d daily=%d weekly=%d",
             len(symbols), stats.get("analyzed", 0), len(failed),
             stats.get("validated_setups", 0), len(weeklies))
    print("\n".join(lines), flush=True)
    if stats.get("analyzed", 0) == 0:
        raise RuntimeError("No stocks were actually analyzed")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
