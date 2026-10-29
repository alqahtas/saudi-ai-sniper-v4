
"""Saudi AI Sniper V5 - TASI market scanner.
Historical/delayed data only. No automatic orders.
"""

import json
import os
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

warnings.filterwarnings(
    "ignore",
    message=r"The inplace keyword .* is deprecated",
    module=r"yfinance\..*",
)

BASE_URL = "https://api.sahmk.sa/api/v1"
MIN_LIQUIDITY_M_SAR = 20.0


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
            params={
                "market": "TASI",
                "limit": 500,
                "offset": offset,
            },
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

            symbol = str(
                company.get("symbol", "")
            ).strip()

            if (
                company.get("market") == "TASI"
                and str(
                    company.get("status", "")
                ).lower() == "active"
                and str(
                    company.get("security_type", "")
                ).lower() == "equity"
                and company.get("is_etf") is not True
                and symbol.isdigit()
            ):
                symbols[symbol] = (
                    company.get("name_ar")
                    or company.get("name_en")
                    or symbol
                )

        offset += len(rows)
        total = payload.get("total")

        if not rows or (
            isinstance(total, int)
            and offset >= total
        ) or len(rows) < 500:
            break
    else:
        raise RuntimeError("Too many directory pages")

    if not symbols:
        raise RuntimeError("No TASI stocks found")

    return symbols


def scan_universe(symbols, max_candidates=50):
    from sniper_core import daily, calculate

    stats = Counter()
    failures = []
    scored = []

    for index, (symbol, company) in enumerate(
        symbols.items(), 1
    ):
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
            item["اسم_الشركة"] = company

            score = float(
                item.get("درجة_الجودة") or 0
            )
            liquidity = float(
                item.get(
                    "قيمة_التداول_اليومية_المتوسطة_مليون"
                ) or 0
            )

            liquid = (
                liquidity >= MIN_LIQUIDITY_M_SAR
            )

            if liquid:
                stats["liquid"] += 1
            else:
                stats["below_liquidity_threshold"] += 1

            validated = (
                liquid
                and item.get("الحالة") == "فرصة مشروطة"
            )

            if validated:
                stats["validated_setups"] += 1

            item["تصنيف_الماسح"] = (
                "فرصة مشروطة"
                if validated
                else "مراقبة فقط"
            )
            item["مستوفي_شرط_السيولة"] = liquid

            scored.append((
                validated,
                liquid,
                score,
                liquidity,
                item,
            ))

        except Exception as exc:
            stats["exceptions"] += 1
            failures.append(symbol)
            print(
                f"WARN {symbol}: "
                f"{type(exc).__name__}: "
                f"{str(exc)[:120]}",
                flush=True,
            )

        if index % 50 == 0:
            print(
                f"Progress: {index}/{len(symbols)}; "
                f"analyzed={stats['analyzed']}; "
                f"failed={len(failures)}",
                flush=True,
            )

    scored.sort(
        key=lambda row: row[:4],
        reverse=True,
    )

    top = [
        row[4]
        for row in scored[:max(0, int(max_candidates))]
    ]

    stats["universe_size"] = len(symbols)
    stats["failed_total"] = len(failures)
    stats["watchlist_returned"] = len(top)
    stats["watchlist_liquid"] = sum(
        1 for item in top
        if item["مستوفي_شرط_السيولة"]
    )

    return top, failures, dict(stats)


def rank_universe(symbols, max_candidates=50):
    ranked, failed, _ = scan_universe(
        symbols, max_candidates
    )
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
            response = session.get(
                f"{BASE_URL}/quote/{symbol}/",
                timeout=20,
            )

            if response.status_code in (401, 403, 429):
                print(
                    f"SAHMK HTTP {response.status_code}"
                )
                break

            response.raise_for_status()
            payload = response.json()

            if (
                not isinstance(payload, dict)
                or str(payload.get("symbol")) != symbol
            ):
                continue

            quotes[symbol] = {
                key: payload.get(key)
                for key in (
                    "price",
                    "volume",
                    "updated_at",
                    "is_delayed",
                )
            }

        except requests.RequestException as exc:
            print(
                f"WARN quote {symbol}: "
                f"{type(exc).__name__}"
            )

    return quotes


def run(max_candidates=50, quote_budget=0):
    symbols = tasi_equities()

    ranked, failed, stats = scan_universe(
        symbols, max_candidates
    )

    quotes = verify_quotes(
        ranked, budget=quote_budget
    )

    report = {
        "scanned_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),
        "market": "TASI",
        "universe_size": len(symbols),
        "summary": stats,
        "historical_candidates": ranked,
        "historical_failures": failed,
        "sahmk_quotes": quotes,
        "note": (
            "Watchlist is not a buy signal. "
            "Historical/delayed data only."
        ),
    }

    Path("market_scan_result.json").write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    lines = [
        "SAUDI AI SNIPER - FULL MARKET SCAN",
        f"Discovered: {len(symbols)}",
        f"Actually analyzed: {stats.get('analyzed', 0)}",
        f"Missing history: {stats.get('missing_history', 0)}",
        f"Analysis failures: {stats.get('analysis_failed', 0)}",
        f"Exceptions: {stats.get('exceptions', 0)}",
        f"Historical failures total: {len(failed)}",
        f"Passed liquidity filter: {stats.get('liquid', 0)}",
        f"Below liquidity filter: {stats.get('below_liquidity_threshold', 0)}",
        f"Validated setups: {stats.get('validated_setups', 0)}",
        f"Watchlist returned: {len(ranked)}",
        f"SAHMK quotes retrieved: {len(quotes)}",
        "TOP 10 WATCHLIST:",
    ]

    for item in ranked[:10]:
        lines.append(
            f"{item.get('السهم')} | "
            f"score={item.get('درجة_الجودة')} | "
            f"{item['تصنيف_الماسح']}"
        )

    Path("market_scan_summary.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print("\n".join(lines), flush=True)

    if stats.get("analyzed", 0) == 0:
        raise RuntimeError(
            "No stocks were actually analyzed"
        )

    return report


if __name__ == "__main__":
    run()
