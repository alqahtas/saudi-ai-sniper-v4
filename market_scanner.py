"""Saudi AI Sniper V5: TASI universe discovery and research candidate selection.

Does not place orders, send Telegram notifications, or promise live pricing.
Free SAHMK plan: directory is used for universe discovery; individual quotes
are optional and limited by a configurable budget. Historical data still
comes from existing sniper_core.daily() / calculate().
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://api.sahmk.sa/api/v1"
MAX_DIRECTORY_PAGES = 10
DEFAULT_QUOTE_BUDGET = 5


def _session():
    s = requests.Session()
    key = os.getenv("SAHMK_API_KEY", "").strip()
    if key:
        s.headers.update({"X-API-Key": key})
    return s


def tasi_equities(session=None):
    """Retrieve active TASI equities only; fail closed on missing API access."""
    if not os.getenv("SAHMK_API_KEY", "").strip() and session is None:
        raise RuntimeError("SAHMK_API_KEY is missing; add it to GitHub Actions secrets")
    session = session or _session()
    symbols = {}
    offset = 0
    for _ in range(MAX_DIRECTORY_PAGES):
        response = session.get(
            f"{BASE_URL}/companies/",
            params={"market": "TASI", "limit": 500, "offset": offset},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise RuntimeError("Unexpected SAHMK company directory response")
        rows = payload["results"]
        for item in rows:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol", "")).strip()
            if (
                item.get("market") == "TASI"
                and str(item.get("status", "")).lower() == "active"
                and str(item.get("security_type", "")).lower() == "equity"
                and item.get("is_etf") is not True
                and symbol.isdigit()
            ):
                symbols[symbol] = item.get("name_ar") or item.get("name_en") or symbol
        offset += len(rows)
        total = payload.get("total")
        if not rows or (isinstance(total, int) and offset >= total) or len(rows) < 500:
            break
    else:
        raise RuntimeError("SAHMK company directory pagination exceeded safety cap")
    if not symbols:
        raise RuntimeError("No valid TASI equities returned; refusing to use an empty list")
    return symbols


def rank_universe(symbols, max_candidates=50):
    """Rank historical setups; a high score is not a validated buy signal."""
    from sniper_core import daily, calculate

    candidates = []
    errors = []
    for symbol, name in symbols.items():
        try:
            candles = daily(symbol)
            if candles is None or candles.empty:
                errors.append(symbol)
                continue
            item = calculate(symbol, candles)
            if not item:
                errors.append(symbol)
                continue
            score = float(item.get("Ø¯Ø±Ø¬Ø©_Ø§ÙØ¬ÙØ¯Ø©") or 0)
            liquidity = float(item.get("ÙÙÙØ©_Ø§ÙØªØ¯Ø§ÙÙ_Ø§ÙÙÙÙÙØ©_Ø§ÙÙØªÙØ³Ø·Ø©_ÙÙÙÙÙ") or 0)
            item = dict(item)
            item["Ø§Ø³Ù_Ø§ÙØ´Ø±ÙØ©"] = name
            if liquidity >= 20:
                candidates.append((score, liquidity, item))
        except Exception as exc:
            errors.append(symbol)
            print(f"WARN {symbol}: {type(exc).__name__}")
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [x[2] for x in candidates[:max_candidates]], errors


def verify_quotes(candidates, session=None, budget=DEFAULT_QUOTE_BUDGET):
    """Optional individual SAHMK delayed quotes, max `budget` API calls.

    Never upgrades historical candidate to 'buy' from an unverified or delayed quote.
    """
    if not os.getenv("SAHMK_API_KEY", "").strip() and session is None:
        raise RuntimeError("SAHMK_API_KEY missing")
    session = session or _session()
    budget = max(0, min(int(budget), 20))
    quotes = {}
    for item in candidates[:budget]:
        symbol = str(item["Ø§ÙØ³ÙÙ"])
        try:
            resp = session.get(f"{BASE_URL}/quote/{symbol}/", timeout=20)
            if resp.status_code in (401, 403, 429):
                print(f"SAHMK quote budget/permission limit: HTTP {resp.status_code}; stopped")
                break
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or str(data.get("symbol")) != symbol:
                continue
            quotes[symbol] = {
                "price": data.get("price"), "volume": data.get("volume"),
                "updated_at": data.get("updated_at"),
                "is_delayed": data.get("is_delayed", True),
            }
        except requests.RequestException as exc:
            print(f"WARN quote {symbol}: {type(exc).__name__}")
    return quotes


def run(max_candidates=50, quote_budget=DEFAULT_QUOTE_BUDGET):
    symbols = tasi_equities()
    ranked, failed = rank_universe(symbols, max_candidates=max_candidates)
    quotes = verify_quotes(ranked, budget=quote_budget)
    result = {
        "scanned_at_utc": datetime.now(timezone.utc).isoformat(),
        "market": "TASI", "universe_size": len(symbols),
        "historical_candidates": ranked, "historical_failures": failed,
        "sahmk_quotes": quotes,
        "note": "Research only: SAHMK Free quotes are delayed; no orders or Telegram messages sent.",
    }
    Path("market_scan_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TASI equities: {len(symbols)} | selected: {len(ranked)} | historical failures: {len(failed)} | SAHMK quotes: {len(quotes)}")
    return result


if __name__ == "__main__":
    run()
