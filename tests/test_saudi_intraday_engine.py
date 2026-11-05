import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from saudi_intraday_engine import completed_candles, evaluate_setup, verified_quote


NOW = datetime(2026, 11, 5, 10, 31, tzinfo=timezone.utc)


def candles(kind="flat", count=80):
    index = pd.date_range(end=NOW - timedelta(minutes=16), periods=count, freq="15min", tz="UTC")
    close = 100.0 + np.sin(np.arange(count) * 0.7) * 0.30
    volume = np.full(count, 1000.0)
    if kind == "breakout":
        close[-1] = 100.80
        volume[-1] = 3500
    elif kind == "rise_only":
        close[-1] = 100.80
        volume[-1] = 900
    frame = pd.DataFrame({
        "Open": close - 0.15,
        "High": close + 0.25,
        "Low": close - 0.25,
        "Close": close,
        "Volume": volume,
    }, index=index)
    if kind == "breakout":
        frame.iloc[-1, frame.columns.get_loc("High")] = 100.95
    return frame


def quote(price=100.75, delayed=False, age=10):
    return {
        "symbol": "2222", "price": price, "volume": 2_000_000,
        "updated_at": (NOW - timedelta(seconds=age)).isoformat(),
        "is_delayed": delayed,
    }


DEPTH = {"bids": [[100.9, 2000], [100.8, 1500]], "asks": [[101.0, 1000], [101.1, 900]]}
TRADES = {"trades": [
    {"price": 101, "quantity": 100, "side": "buy"},
    {"price": 101, "quantity": 35, "side": "sell"},
]}
MARKET = {"index_change_percent": 0.4, "advancing": 150, "declining": 80}


class SaudiIntradayEngineTests(unittest.TestCase):
    def test_delayed_or_stale_quote_is_rejected(self):
        self.assertIsNone(verified_quote(quote(delayed=True), now=NOW))
        self.assertIsNone(verified_quote(quote(age=500), now=NOW))

    def test_open_candle_is_removed_by_timestamp(self):
        frame = candles()
        open_stamp = pd.Timestamp(NOW - timedelta(minutes=5))
        frame.loc[open_stamp] = frame.iloc[-1]
        result = completed_candles(frame, now=NOW)
        self.assertNotIn(open_stamp, result.index)
        self.assertEqual(len(result), 80)

    def test_completed_breakout_with_volume_depth_and_flow_is_accepted(self):
        result = evaluate_setup(
            "2222", "أرامكو", candles("breakout"), quote(), DEPTH, TRADES, MARKET, now=NOW
        )
        self.assertIsNotNone(result)
        self.assertIn("اختراق", result.signal)
        self.assertGreaterEqual(result.entry_low, result.support)

    def test_price_rise_without_volume_is_not_a_signal(self):
        result = evaluate_setup(
            "2222", "أرامكو", candles("rise_only"), quote(), DEPTH, TRADES, MARKET, now=NOW
        )
        self.assertIsNone(result)

    def test_negative_depth_rejects_breakout(self):
        weak_depth = {"bids": [[100.9, 100]], "asks": [[101.0, 5000]]}
        result = evaluate_setup(
            "2222", "أرامكو", candles("breakout"), quote(), weak_depth, TRADES, MARKET, now=NOW
        )
        self.assertIsNone(result)

    def test_engine_has_no_order_execution_calls(self):
        tree = ast.parse(Path("saudi_intraday_engine.py").read_text(encoding="utf-8"))
        forbidden = {"submit_order", "place_order", "cancel_order", "buy", "sell"}
        calls = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(forbidden.isdisjoint(calls))


if __name__ == "__main__":
    unittest.main()
