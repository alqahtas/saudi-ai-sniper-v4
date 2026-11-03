import unittest
from datetime import date

from sahmk_intelligence import (
    CACHE, GLOBAL_CALLS, PlanLimitError, SahmkProClient, depth_metrics,
    fetch_intelligence, format_pro_sections, intraday_metrics,
    trade_flow_metrics,
)


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        self.last_args = args
        self.last_kwargs = kwargs
        return self.responses.pop(0)


class PartialClient:
    def quote(self, symbol):
        return {"price": 30.1, "is_delayed": False, "updated_at": "2026-11-03T12:00:00Z"}

    def depth(self, symbol, levels=5):
        raise PlanLimitError("PLAN_LIMIT")

    def trades(self, symbol, limit=100):
        return {"trades": [{"price": 30, "quantity": 10, "side": "buy"}]}

    def intraday(self, symbol, interval="60min"):
        return {"candles": []}

    def company(self, symbol):
        return {}

    def ratios(self, symbol):
        return {"pe_ratio": 14}

    def financials(self, symbol):
        return {}

    def dividends(self, symbol):
        return {"dividend_yield_ttm": 4.2}

    def market(self):
        return {"index_value": 12000, "index_change_percent": 0.5}

    def events(self, symbol):
        return {}


class SahmkIntelligenceTests(unittest.TestCase):
    def setUp(self):
        CACHE.clear()
        GLOBAL_CALLS.clear()

    def test_depth_bid_heavy_and_spread(self):
        result = depth_metrics({
            "data": {
                "bids": [{"price": 10.00, "quantity": 300}, {"price": 9.99, "quantity": 200}],
                "asks": [{"price": 10.02, "quantity": 100}, {"price": 10.03, "quantity": 50}],
            }
        })
        self.assertGreater(result["imbalance"], 1.35)
        self.assertAlmostEqual(result["spread"], 0.02)
        self.assertIn("الشراء أقوى", result["reading"])
        self.assertEqual(len(result["bids"]), 2)

    def test_depth_empty_is_safe(self):
        result = depth_metrics({})
        self.assertIsNone(result["imbalance"])
        self.assertIn("غير متوفر", result["reading"])

    def test_trade_flow_uses_aggressor_side(self):
        result = trade_flow_metrics({"trades": [
            {"price": 20, "quantity": 100, "side": "buy"},
            {"price": 20, "quantity": 20, "side": "sell"},
        ]})
        self.assertAlmostEqual(result["buy_ratio"], 100 / 120)
        self.assertIn("تميل للشراء", result["reading"])

    def test_intraday_excludes_current_candle(self):
        candles = [
            {"high": 10 + i * .1, "low": 9 + i * .1, "close": 9.5 + i * .1, "volume": 100}
            for i in range(8)
        ]
        result = intraday_metrics({"candles": candles})
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["support"], 9.0)
        self.assertEqual(result["resistance"], 10.6)
        self.assertEqual(result["trend"], "صاعد")

    def test_intraday_window_stays_within_pro_limit(self):
        session = FakeSession([FakeResponse(200, {"candles": []})])
        client = SahmkProClient(session=session, sleep=lambda _: None, now=lambda: 1000)
        client.intraday("2222")
        params = session.last_kwargs["params"]
        start = date.fromisoformat(params["from"])
        end = date.fromisoformat(params["to"])
        self.assertLessEqual((end - start).days + 1, 90)
        self.assertEqual(params["interval"], "60min")

    def test_429_honours_retry_after_then_succeeds(self):
        waits = []
        session = FakeSession([
            FakeResponse(429, {}, {"Retry-After": "3"}),
            FakeResponse(200, {"ok": True}),
        ])
        client = SahmkProClient(session=session, sleep=waits.append, now=lambda: 1000)
        value = client._get("/example/")
        self.assertEqual(value, {"ok": True})
        self.assertEqual(waits, [3.0])
        self.assertEqual(session.calls, 2)

    def test_plan_limit_does_not_remove_other_blocks(self):
        bundle = fetch_intelligence("2222", PartialClient())
        self.assertIn("quote", bundle["data"])
        self.assertIn("trades", bundle["data"])
        self.assertIn("depth", bundle["errors"])
        message = format_pro_sections(bundle)
        self.assertIn("بيانات SAHMK Pro", message)
        self.assertIn("غير مفعّل", message)
        self.assertIn("14.00", message)
        self.assertIn("4.20%", message)


if __name__ == "__main__":
    unittest.main()
