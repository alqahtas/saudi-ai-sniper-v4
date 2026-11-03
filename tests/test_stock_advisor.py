import unittest
import numpy as np
import pandas as pd

from stock_advisor import confirmed_pivots, merge_levels, valuation_verdict, build_recommendation


class AdvisorTests(unittest.TestCase):
    def candles(self):
        n = 260
        close = np.linspace(80, 110, n) + np.sin(np.arange(n) / 5)
        return pd.DataFrame({
            "Open": close - .2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_500_000, n),
        }, index=pd.date_range("2025-01-01", periods=n))

    def test_last_two_candles_are_not_pivots(self):
        data = self.candles().tail(10).copy()
        data.iloc[-1, data.columns.get_loc("High")] = 999
        _, highs = confirmed_pivots(data)
        self.assertNotIn(999, highs)

    def test_merge_nearby_levels(self):
        self.assertEqual(len(merge_levels([100, 100.4, 103])), 2)

    def test_valuation_rules(self):
        verdict, _ = valuation_verdict({
            "price_to_earnings": 12, "price_to_book": 2,
            "return_on_equity_reported": 15, "net_profit_margin_percent": 8,
            "debt_to_equity": .8, "annual_net_income_reported": 100,
        })
        self.assertEqual(verdict, "جذاب نسبيًا")
        verdict, _ = valuation_verdict({"price_to_earnings": 40})
        self.assertEqual(verdict, "مرتفع")

    def test_plan_entry_below_price_and_stop_two_percent(self):
        plan = build_recommendation(self.candles(), {"price": 110})
        self.assertLess(plan["entry"], 110)
        self.assertAlmostEqual(plan["stop"], round(plan["entry"] * .98, 2), places=2)
        self.assertGreater(plan["target1"], plan["entry"])
        self.assertGreater(plan["target2"], plan["target1"])


if __name__ == "__main__":
    unittest.main()
