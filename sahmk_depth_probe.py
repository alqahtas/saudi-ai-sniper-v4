"""SAHMK realtime market-depth entitlement and snapshot probe (read-only).

Documented channel: wss://api.sahmk.sa/ws/v1/market/depth/?api_key=...
Do not log keys or raw customer depth. Verify connected, subscribed, and depth_snapshot.
"""
import json
import os
import time
from urllib.parse import quote

URL = "wss://api.sahmk.sa/ws/v1/market/depth/"

def parse_entitlement(message):
    if message.get("type") != "connected" or message.get("channel") != "depth":
        raise ValueError("Expected connected acknowledgement for depth")
    levels = int(message.get("entitled_levels") or 0)
    if levels < 1:
        raise PermissionError("No market-depth levels entitled")
    limits = message.get("limits") or {}
    max_per_call = int(limits.get("max_symbols_per_call") or 1)
    if max_per_call < 1:
        raise ValueError("Invalid connection limit")
    # Start with 5, even when entitlement allows 20. Never request inaccessible data.
    return min(levels, 5), levels

def probe(symbol="2222", timeout_seconds=24, socket_factory=None):
    if not str(symbol).isdigit():
        raise ValueError("Stock symbol must be numeric")
    key = os.environ.get("SAHMK_API_KEY", "").strip()
    if not key:
        raise RuntimeError("SAHMK_API_KEY secret missing")
    if socket_factory is None:
        from websocket import create_connection, WebSocketTimeoutException
        socket_factory = create_connection
    else:
        from websocket import WebSocketTimeoutException
    ws = socket_factory(URL + "?api_key=" + quote(key, safe=""), timeout=12)
    connected = subscribed = snapshot = False
    entitled = requested = None
    started = time.monotonic()
    try:
        ws.settimeout(3)
        while time.monotonic() - started < timeout_seconds:
            try:
                raw = ws.recv()
            except WebSocketTimeoutException:
                continue
            if not raw:
                raise RuntimeError("Depth stream closed before snapshot")
            packet = json.loads(raw)
            typ = packet.get("type")
            if typ == "error":
                # An explicit error code can be helpful; never print the raw payload.
                raise PermissionError("Depth server error code=" + str(packet.get("code","unknown"))[:60])
            if typ == "connected":
                requested, entitled = parse_entitlement(packet)
                connected = True
                print("SAHMK_DEPTH_CONNECTED plan=" + str(packet.get("plan","unknown"))[:40]
                      + " entitled_levels=" + str(entitled), flush=True)
                ws.send(json.dumps({"action":"subscribe","symbols":[str(symbol)],
                                     "levels": requested}))
            elif typ == "subscribed":
                subscribed = str(symbol) in [str(s) for s in packet.get("symbols",[])]
                print("SAHMK_DEPTH_SUBSCRIBED confirmed=" + str(subscribed), flush=True)
            elif typ == "depth_snapshot" and str(packet.get("symbol") or (packet.get("data") or {}).get("symbol")) == str(symbol):
                snapshot = True
                print("SAHMK_DEPTH_SNAPSHOT symbol=" + str(symbol) + " received=true", flush=True)
            if connected and subscribed and snapshot:
                print("SAHMK_DEPTH_ACCESS_OK symbol=" + str(symbol)
                      + " requested_levels=" + str(requested)
                      + " entitled_levels=" + str(entitled), flush=True)
                return {"symbol":str(symbol),"requested_levels":requested,"entitled_levels":entitled,
                        "connected":True,"subscribed":True,"snapshot":True}
        raise TimeoutError("Depth channel has not confirmed connected + subscribed + depth_snapshot")
    finally:
        ws.close()

if __name__ == "__main__":
    probe()
