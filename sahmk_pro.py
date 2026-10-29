"""Saudi AI Sniper V5: SAHMK market-data adapter (read-only).

REST helpers preserve the older scanner interface. Streaming is OPT-IN:
call stream_stock_quotes(symbols, on_quote) from a dedicated worker.
Requires: pip install websocket-client
Do not treat a quote update alone as a confirmed trading signal.
"""
import json
import logging
import os
import time
from urllib.parse import quote as url_quote

import requests

BASE_URL = "https://api.sahmk.sa/api/v1"
STOCK_WS_URL = "wss://api.sahmk.sa/ws/v1/stocks/"
LOG = logging.getLogger(__name__)


def _api_key():
    key = os.getenv("SAHMK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SAHMK_API_KEY is missing")
    return key


def api_request(path, params=None):
    if not path.startswith("/"):
        raise ValueError("API path must start with /")
    response = requests.get(
        BASE_URL + path,
        headers={"X-API-Key": _api_key()},
        params=params,
        timeout=25,
    )
    response.raise_for_status()
    return response.json()


def tasi_market():
    """TASI summary: check is_delayed and updated_at before using."""
    return api_request("/market/summary/", {"index": "TASI", "data_mode": "realtime"})


def stock_quote(symbol):
    """Quote: do not assume data is live; inspect is_delayed."""
    symbol = str(symbol).strip()
    if not symbol.isdigit():
        raise ValueError("Invalid stock symbol")
    return api_request(f"/quote/{symbol}/", {"data_mode": "realtime"})


def bulk_quotes(symbols, batch_size=40):
    """Preserve historical return shape: list of responses."""
    size = max(1, min(int(batch_size), 40))
    cleaned = [str(x).strip() for x in symbols]
    if any(not x.isdigit() for x in cleaned):
        raise ValueError("Invalid stock symbol")
    results = []
    for start in range(0, len(cleaned), size):
        batch = cleaned[start:start + size]
        if batch:
            results.append(api_request("/quotes/", {"symbols": ",".join(batch)}))
    return results


def latest_trades(symbol, limit=20):
    symbol = str(symbol).strip()
    if not symbol.isdigit():
        raise ValueError("Invalid stock symbol")
    return api_request(f"/market/trades/{symbol}/", {"limit": max(1, min(int(limit), 200))})


def market_direction(data):
    if not isinstance(data, dict):
        return "ØºÙØ± ÙØ¹Ø±ÙÙ - Ø¨ÙØ§ÙØ§Øª ØºÙØ± ØµØ§ÙØ­Ø©"
    try:
        if any(data.get(k) is None for k in ("advancing", "declining", "index_change_percent")):
            return "ØºÙØ± ÙØ¹Ø±ÙÙ - Ø¨ÙØ§ÙØ§Øª ÙØ§ÙØµØ©"
        advancing = int(data["advancing"])
        declining = int(data["declining"])
        change = float(data["index_change_percent"])
    except (ValueError, TypeError):
        return "ØºÙØ± ÙØ¹Ø±ÙÙ - Ø¨ÙØ§ÙØ§Øª ÙØ§ÙØµØ©"
    if advancing + declining == 0:
        return "ØºÙØ± ÙØ¹Ø±ÙÙ - Ø¨ÙØ§ÙØ§Øª ÙØ§ÙØµØ©"
    if advancing > declining * 1.4 and change > 0:
        return "Ø¥ÙØ¬Ø§Ø¨Ù - Ø§ÙØªØ´Ø§Ø± ØµØ¹ÙØ¯ ÙØ§Ø³Ø¹"
    if declining > advancing * 1.4 and change < 0:
        return "Ø³ÙØ¨Ù - Ø§ÙØªØ´Ø§Ø± ÙØ¨ÙØ· ÙØ§Ø³Ø¹"
    return "ÙØ®ØªÙØ· - ÙØ­ØªØ§Ø¬ ØªØ£ÙÙØ¯"


def stream_stock_quotes(symbols, on_quote, *, max_retries=None):
    """Subscribe to up to 60 symbols on a single Pro stocks WebSocket.

    on_quote(symbol, quote_data, message) called once per quote event.
    Reconnects with backoff on transient failures. Auth/permission errors stop.
    For >60 symbols use separately budgeted connections and plan limits.
    This function blocks, so run it only in a background worker.
    """
    try:
        import websocket  # pip install websocket-client
    except ImportError as exc:
        raise RuntimeError("Install websocket-client in requirements.txt") from exc

    if not callable(on_quote):
        raise TypeError("on_quote must be callable")
    cleaned = list(dict.fromkeys(str(s).strip() for s in symbols))
    if not cleaned or any(not s.isdigit() for s in cleaned):
        raise ValueError("Provide valid numeric stock symbols")
    if len(cleaned) > 60:
        raise ValueError("Pro limit: at most 60 symbols per stock connection")

    key = _api_key()
    url = STOCK_WS_URL + "?api_key=" + url_quote(key, safe="")
    attempt = 0
    while max_retries is None or attempt <= max_retries:
        state = {"fatal": False, "connected": False}

        def on_open(ws):
            LOG.info("SAHMK stock stream socket opened")

        def on_message(ws, raw):
            try:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "connected":
                    state["connected"] = True
                    limits = message.get("limits") or {}
                    per_call = min(int(limits.get("max_symbols_per_call") or 20), 20)
                    max_symbols = int(limits.get("max_symbols_per_connection") or 60)
                    if len(cleaned) > max_symbols:
                        state["fatal"] = True
                        LOG.error("Connection permits only %s symbols", max_symbols)
                        ws.close()
                        return
                    for i in range(0, len(cleaned), per_call):
                        ws.send(json.dumps({"action": "subscribe", "symbols": cleaned[i:i + per_call]}))
                    LOG.info("Subscribed to %d stock symbols", len(cleaned))
                elif kind == "quote":
                    symbol = str(message.get("symbol") or "")
                    data = message.get("data")
                    if symbol in cleaned and isinstance(data, dict):
                        try:
                            on_quote(symbol, data, message)
                        except Exception:
                            LOG.exception("Quote callback error for %s", symbol)
                elif kind == "error":
                    code = str(message.get("code") or "")
                    LOG.error("SAHMK stream error: %s", code)
                    if "ENTITLEMENT" in code or "AUTH" in code:
                        state["fatal"] = True
                        ws.close()
            except (ValueError, TypeError, KeyError):
                LOG.exception("Malformed SAHMK socket message")

        def on_error(ws, error):
            LOG.warning("SAHMK socket disconnected: %s", type(error).__name__)

        def on_close(ws, code, reason):
            if code in (4401, 4403):
                state["fatal"] = True
            LOG.warning("SAHMK socket closed (code=%s)", code)

        app = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        app.run_forever(ping_interval=30, ping_timeout=10)
        if state["fatal"]:
            raise RuntimeError("SAHMK stream authentication/entitlement/plan rejected")
        attempt += 1
        if max_retries is not None and attempt > max_retries:
            break
        time.sleep(min(60, 2 ** min(attempt, 6)))


def test_connection():
    market = tasi_market()
    quote = stock_quote("2010")
    print("TASI:", market.get("index_value"))
    print("TASI market:", market_direction(market))
    print("TASI delayed:", market.get("is_delayed"))
    print("SABIC price:", quote.get("price"))
    print("SABIC delayed:", quote.get("is_delayed"))
    print("SAHMK REST TEST COMPLETED")


if __name__ == "__main__":
    test_connection()
