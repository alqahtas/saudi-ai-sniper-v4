
"""SAHMK Pro data provider for Saudi AI Sniper.
Read-only market data. No trading orders.
"""

import os
import requests

BASE_URL = "https://api.sahmk.sa/api/v1"


def api_request(path, params=None):
    key = os.getenv("SAHMK_API_KEY", "").strip()

    if not key:
        raise RuntimeError("SAHMK_API_KEY is missing")

    response = requests.get(
        BASE_URL + path,
        headers={"X-API-Key": key},
        params=params,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def tasi_market():
    """TASI index and whole-market strength."""
    return api_request(
        "/market/summary/",
        {"index": "TASI", "data_mode": "realtime"},
    )


def stock_quote(symbol):
    """Real-time quote for one stock."""
    return api_request(
        f"/quote/{symbol}/",
        {"data_mode": "realtime"},
    )


def bulk_quotes(symbols, batch_size=40):
    """Fetch several stock quotes in batches."""
    results = []

    for start in range(0, len(symbols), batch_size):
        batch = symbols[start:start + batch_size]

        data = api_request(
            "/quotes/",
            {"symbols": ",".join(map(str, batch))},
        )

        results.append(data)

    return results


def latest_trades(symbol, limit=20):
    """Fetch recent executed trades."""
    return api_request(
        f"/market/trades/{symbol}/",
        {"limit": min(int(limit), 200)},
    )


def market_direction(data):
    """Classify market conditions conservatively."""
    advancing = int(data.get("advancing") or 0)
    declining = int(data.get("declining") or 0)
    change = float(
        data.get("index_change_percent") or 0
    )

    if advancing > declining * 1.4 and change > 0:
        return "إيجابي - انتشار صعود واسع"

    if declining > advancing * 1.4 and change < 0:
        return "سلبي - انتشار هبوط واسع"

    return "مختلط - يحتاج تأكيد"


def test_connection():
    market = tasi_market()
    sabic = stock_quote("2010")

    print("=== SAHMK PRO TEST ===")
    print("TASI:", market.get("index_value"))
    print(
        "TASI change %:",
        market.get("index_change_percent"),
    )
    print("Advancing:", market.get("advancing"))
    print("Declining:", market.get("declining"))
    print("Market:", market_direction(market))
    print(
        "TASI delayed:",
        market.get("is_delayed"),
    )
    print("SABIC price:", sabic.get("price"))
    print(
        "SABIC delayed:",
        sabic.get("is_delayed"),
    )
    print("SAHMK PRO TEST COMPLETED")


if __name__ == "__main__":
    test_connection()
