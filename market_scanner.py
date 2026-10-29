"""Saudi AI Sniper V5 - TASI daily scanner + confirmed weekly swing setups.

Uses historical candle data; NOT a real-time trading signal or order system.
Weekly strategies use completed weekly candles only.
"""

import json
import os
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

warnings.filterwarnings("ignore", message=r"The inplace keyword .* is deprecated", module=r"yfinance\..*")
BASE_URL = "https://api.sahmk.sa/api/v1"
MIN_LIQUIDITY_M_SAR = 20.0
TZ = ZoneInfo("Asia/Riyadh")


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
        rows = payload.get("results")
        if not isinstance(rows, list):
            raise RuntimeError("Invalid SAHMK response")
        for company in rows:
            if not isinstance(company, dict):
                continue
            symbol = str(company.get("symbol", "")).strip()
            if (company.get("market") == "TASI"
                    and str(company.get("status", "")).lower() == "active"
                    and str(company.get("security_type", "")).lower() == "equity"
                    and company.get("is_etf") is not True and symbol.isdigit()):
                symbols[symbol] = company.get("name_ar") or company.get("name_en") or symbol
        offset += len(rows)
        total = payload.get("total")
        if not rows or (isinstance(total, int) and offset >= total) or len(rows) < 500:
            break
    else:
        raise RuntimeError("Too many directory pages")
    if not symbols:
        raise RuntimeError("No TASI stocks found")
    return symbols


def normalized_daily(candles):
    """Prepare daily OHLCV for weekly aggregation; returns None for unknown formats."""
    import pandas as pd
    if candles is None or candles.empty:
        return None
    df = candles.copy()
    # Support index-based dates and the common Date/Datetime column layout.
    if not isinstance(df.index, pd.DatetimeIndex):
        date_col = next((c for c in df.columns if str(c).lower() in ("date", "datetime", "timestamp")), None)
        if date_col is None:
            return None
        df.index = pd.to_datetime(df[date_col], errors="coerce", utc=True).dt.tz_localize(None)
    elif df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    if isinstance(df.columns, pd.MultiIndex):
        return None  # avoid silently mixing symbols/columns
    names = {str(c).lower(): c for c in df.columns}
    required = ("open", "high", "low", "close", "volume")
    if not all(k in names for k in required):
        return None
    df = df[[names[k] for k in required]].copy()
    df.columns = list(required)
    for name in required:
        df[name] = pd.to_numeric(df[name], errors="coerce")
    df = df.dropna().sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df if not df.empty else None


def weekly_setup(symbol, company, daily_candles, liquidity):
    """Conservative weekly watch strategy on fully completed Saudi trading weeks.

    A signal requires: weekly upward trend, RSI strength, a close near a recent
    support zone, and above-average weekly volume. This is a *technical setup*,
    not confirmed live execution or a fundamental valuation.
    """
    import pandas as pd
    df = normalized_daily(daily_candles)
    if df is None or len(df) < 160 or liquidity < MIN_LIQUIDITY_M_SAR:
        return None
    weekly = df.resample("W-THU", label="right", closed="right").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()
    # Do not use a partially formed weekly bar (Saudi week ends Thursday).
    now = datetime.now(TZ)
    today = pd.Timestamp(now.date())
    weekly = weekly.loc[weekly.index <= today]
    if now.weekday() == 3 and (now.hour, now.minute) < (15, 0):
        weekly = weekly.loc[weekly.index < today]
    if len(weekly) < 30:
        return None
    close, volume = weekly["close"], weekly["volume"]
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    vol10 = volume.shift(1).rolling(10).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    rsi = 100 - 100 / (1 + rs)
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
    stop = round(base * 0.98, 2)
    risk = price - stop
    if risk <= 0:
        return None
    return {
        "Ø§ÙØ³ÙÙ": symbol, "Ø§Ø³Ù_Ø§ÙØ´Ø±ÙØ©": company,
        "Ø§ÙØ¥Ø·Ø§Ø±_Ø§ÙØ²ÙÙÙ": "weekly", "Ø§ÙØ§Ø³ØªØ±Ø§ØªÙØ¬ÙØ§Øª": "weekly_support",
        "Ø§ÙØ­Ø§ÙØ©": "ÙØ±ØµØ© ÙØ´Ø±ÙØ·Ø©", "ØªØµÙÙÙ_Ø§ÙÙØ§Ø³Ø­": "ÙØ±ØµØ© ÙØ´Ø±ÙØ·Ø©",
        "Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©": 80,
        "Ø¨Ø¯Ø§ÙØ©_ÙØ·Ø§Ù_Ø§ÙØ´Ø±Ø§Ø¡": round(price, 2),
        "ÙÙÙ_ØªÙØ¯ÙØ±Ù": stop,
        "ÙØ¯Ù_ØªÙØ¯ÙØ±Ù": round(price + 1.5 * risk, 2),
        "ÙØ¯Ù_Ø«Ø§Ù": round(price + 2.5 * risk, 2),
        "Ø§ÙØªØ§Ø±ÙØ®": weekly.index[-1].date().isoformat(),
        "ÙÙÙØ©_Ø§ÙØªØ¯Ø§ÙÙ_Ø§ÙÙÙÙÙØ©_Ø§ÙÙØªÙØ³Ø·Ø©_ÙÙÙÙÙ": liquidity,
        "ÙØ³ØªÙÙÙ_Ø´Ø±Ø·_Ø§ÙØ³ÙÙÙØ©": True,
        "Ø³Ø¨Ø¨_Ø§ÙØ¥Ø´Ø§Ø±Ø©": "Ø§ØªØ¬Ø§Ù Ø£Ø³Ø¨ÙØ¹Ù ØµØ§Ø¹Ø¯ + RSI + ÙØ±Ø¨ Ø¯Ø¹Ù + Ø­Ø¬Ù Ø£Ø³Ø¨ÙØ¹Ù ÙØ±ØªÙØ¹",
        "ÙÙØ¹_Ø§ÙØ¨ÙØ§ÙØ§Øª": "Ø´ÙÙØ¹ Ø£Ø³Ø¨ÙØ¹ÙØ© ÙÙØªÙÙØ© - ÙÙØ³Øª Ø£Ø³Ø¹Ø§Ø±ÙØ§ ÙØ­Ø¸ÙØ©",
    }


def scan_universe(symbols, max_candidates=50):
    from sniper_core import daily, calculate
    stats = Counter()
    failures, scored, weeklies = [], [], []
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
            item = dict(result)
            item["Ø§Ø³Ù_Ø§ÙØ´Ø±ÙØ©"] = company
            score = float(item.get("Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©") or 0)
            liquidity = float(item.get("ÙÙÙØ©_Ø§ÙØªØ¯Ø§ÙÙ_Ø§ÙÙÙÙÙØ©_Ø§ÙÙØªÙØ³Ø·Ø©_ÙÙÙÙÙ") or 0)
            liquid = liquidity >= MIN_LIQUIDITY_M_SAR
            stats["liquid" if liquid else "below_liquidity_threshold"] += 1
            validated = liquid and item.get("Ø§ÙØ­Ø§ÙØ©") == "ÙØ±ØµØ© ÙØ´Ø±ÙØ·Ø©"
            if validated:
                stats["validated_setups"] += 1
            item["ØªØµÙÙÙ_Ø§ÙÙØ§Ø³Ø­"] = "ÙØ±ØµØ© ÙØ´Ø±ÙØ·Ø©" if validated else "ÙØ±Ø§ÙØ¨Ø© ÙÙØ·"
            item["ÙØ³ØªÙÙÙ_Ø´Ø±Ø·_Ø§ÙØ³ÙÙÙØ©"] = liquid
            item.setdefault("Ø§ÙØ¥Ø·Ø§Ø±_Ø§ÙØ²ÙÙÙ", "daily")
            scored.append((validated, liquid, score, liquidity, item))
            try:
                weekly = weekly_setup(symbol, company, candles, liquidity)
                if weekly:
                    weeklies.append(weekly)
                    stats["weekly_setups"] += 1
            except Exception as exc:
                stats["weekly_errors"] += 1
                print(f"WARN weekly {symbol}: {type(exc).__name__}: {exc}", flush=True)
        except Exception as exc:
            stats["exceptions"] += 1
            failures.append(symbol)
            print(f"WARN {symbol}: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        if index % 50 == 0:
            print(f"Progress: {index}/{len(symbols)}; analyzed={stats['analyzed']}; failed={len(failures)}", flush=True)
    scored.sort(key=lambda row: row[:4], reverse=True)
    top = [row[4] for row in scored[:max(0, int(max_candidates))]]
    weeklies.sort(key=lambda x: x["Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©"], reverse=True)
    stats.update(universe_size=len(symbols), failed_total=len(failures),
                 watchlist_returned=len(top), watchlist_liquid=sum(1 for item in top if item["ÙØ³ØªÙÙÙ_Ø´Ø±Ø·_Ø§ÙØ³ÙÙÙØ©"]))
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
        symbol = str(item["Ø§ÙØ³ÙÙ"])
        try:
            response = session.get(f"{BASE_URL}/quote/{symbol}/", timeout=20)
            if response.status_code in (401, 403, 429):
                print(f"SAHMK HTTP {response.status_code}")
                break
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("symbol")) != symbol:
                continue
            quotes[symbol] = {key: payload.get(key) for key in ("price", "volume", "updated_at", "is_delayed")}
        except requests.RequestException as exc:
            print(f"WARN quote {symbol}: {type(exc).__name__}")
    return quotes


def run(max_candidates=50, quote_budget=0):
    symbols = tasi_equities()
    ranked, weeklies, failed, stats = scan_universe(symbols, max_candidates)
    quotes = verify_quotes(ranked, budget=quote_budget)
    # Compatible with telegram_scan.py, which reads historical_candidates.
    candidates = (ranked + weeklies)
    report = {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": "TASI", "universe_size": len(symbols), "summary": stats,
        "historical_candidates": candidates, "weekly_candidates": weeklies,
        "historical_failures": failed, "sahmk_quotes": quotes,
        "note": "Daily and completed weekly historical candles. NOT a live entry confirmation.",
    }
    Path("market_scan_result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    lines = ["SAUDI AI SNIPER - DAILY + WEEKLY HISTORICAL SCAN",
             f"Discovered: {len(symbols)}", f"Analyzed: {stats.get('analyzed', 0)}",
             f"Errors: {len(failed)}", f"Daily setups: {stats.get('validated_setups', 0)}",
             f"Weekly setups: {len(weeklies)}", f"Quotes retrieved: {len(quotes)}",
             "TOP 10 DAILY WATCHLIST:"]
    for item in ranked[:10]:
        lines.append(f"{item.get('Ø§ÙØ³ÙÙ')} | score={item.get('Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©')} | {item['ØªØµÙÙÙ_Ø§ÙÙØ§Ø³Ø­']}")
    lines.append("WEEKLY SETUPS:")
    for item in weeklies[:10]:
        lines.append(f"{item['Ø§ÙØ³ÙÙ']} | weekly close={item['Ø¨Ø¯Ø§ÙØ©_ÙØ·Ø§Ù_Ø§ÙØ´Ø±Ø§Ø¡']}")
    Path("market_scan_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines), flush=True)
    if stats.get("analyzed", 0) == 0:
        raise RuntimeError("No stocks were actually analyzed")
    return report


if __name__ == "__main__":
    run()
