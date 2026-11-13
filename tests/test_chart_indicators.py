import json
import unittest
from decimal import Decimal

from chart_indicators import analyze, describe, frame_context, RETRACEMENT_RATIOS, EXTENSION_RATIOS


def bars():
    closes = [110, 111, 120, 111, 110, 108, 100, 105, 108, 110, 115, 112, 111, 109]
    return [{"high": c+1, "low": c-1, "close": c} for c in closes]


class ChartIndicatorsTests(unittest.TestCase):
    def test_exact_user_levels_and_three_anchor_extension(self):
        result = analyze(bars())
        self.assertEqual(tuple(result["retracement"]["levels"]), RETRACEMENT_RATIOS)
        self.assertEqual(tuple(result["extension"]["levels"]), EXTENSION_RATIOS)
        self.assertEqual(result["extension"]["anchors"], [2, 6, 10])
        self.assertAlmostEqual(result["extension"]["levels"]["1.272"], 88.016)
        self.assertAlmostEqual(result["retracement"]["levels"]["0.382"], 105.494)

    def test_breakout_confirmation_and_target_are_directional(self):
        result = analyze(bars())["rebound"]
        self.assertEqual(result["side"], "SHORT")
        self.assertEqual(result["ratio"], "0.382")
        self.assertAlmostEqual(result["trigger"], 109.506)
        self.assertAlmostEqual(result["target_0.786"], 102.638)
        self.assertEqual(result["invalidation"], 116)

    def test_long_is_mirrored_without_using_short_execution(self):
        mirror = [{"high": 220-r["low"], "low": 220-r["high"], "close": 220-r["close"]}
                  for r in bars()]
        result = analyze(mirror)
        self.assertEqual(result["extension"]["side"], "LONG")
        self.assertEqual(result["rebound"]["side"], "LONG")
        self.assertAlmostEqual(result["rebound"]["target_0.786"], 117.362)

    def test_pivot_requires_two_completed_right_neighbors(self):
        self.assertIsNone(analyze(bars()[:12])["extension"])
        self.assertIsNotNone(analyze(bars()[:13])["extension"])

    def test_forming_candle_cannot_change_analysis(self):
        forming = {"high": 10000, "low": 1, "close": 5000, "complete": False}
        self.assertEqual(analyze(bars()), analyze(bars()+[forming]))

    def test_broken_range_does_not_reuse_old_levels(self):
        self.assertIsNone(analyze(bars()+[{"high": 130, "low": 120, "close": 125}])["retracement"])

    def test_ema_requires_140_samples_and_matches_recurrence(self):
        flat = [{"high": 101, "low": 99, "close": 100}]*139
        self.assertEqual(analyze(flat)["ema"], {})
        result = analyze(flat+[{"high": 111, "low": 99, "close": 110}])
        for period in (25, 75, 140):
            self.assertAlmostEqual(result["ema"][str(period)], 100+20/(period+1))
        self.assertEqual(result["ema_stack"], "LONG")

    def test_flat_ema_is_neutral(self):
        result = analyze([{"high": 101, "low": 99, "close": 100}]*140)
        self.assertEqual(result["ema_stack"], "NEUTRAL")

    def test_nonfinite_and_bad_ohlc_are_rejected(self):
        for row in ({"high": 2, "low": 1, "close": Decimal("NaN")},
                    {"high": 2, "low": 1, "close": 3},
                    {"high": "1e999", "low": 1, "close": 2}, {}):
            self.assertEqual(analyze([row])["status"], "invalid_ohlc")

    def test_frame_adapter_filters_open_and_future_candles(self):
        rows = [dict(r, close_time=i) for i, r in enumerate(bars())]
        result = frame_context({"1h": rows+[dict(rows[-1], close_time=100)]}, 20)
        self.assertEqual(result["1h"], analyze(bars()))

    def test_json_and_compact_alert_output(self):
        result = analyze(bars())
        json.dumps(result, allow_nan=False)
        self.assertIn("127.2%", describe(result))
        self.assertLess(len(describe(result)), 600)
