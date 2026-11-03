import unittest

import pandas as pd

from timeframe_analysis import format_timeframe_table


def payload(index, start=10.0, step=0.02):
    rows = []
    for number, stamp in enumerate(index):
        close = start + number * step
        rows.append({
            "timestamp": stamp.isoformat(),
            "open": close - 0.01,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "volume": 1000 + number,
        })
    return {"data": rows}


class TimeframeAnalysisTests(unittest.TestCase):
    def setUp(self):
        daily_index = pd.date_range("2025-01-01", periods=500, freq="B")
        self.daily = pd.DataFrame(
            {
                "Open": [20 + i * 0.02 for i in range(500)],
                "High": [20.1 + i * 0.02 for i in range(500)],
                "Low": [19.9 + i * 0.02 for i in range(500)],
                "Close": [20.05 + i * 0.02 for i in range(500)],
                "Volume": [1_000_000 + i for i in range(500)],
            },
            index=daily_index,
        )
        hourly_index = pd.date_range("2026-09-01", periods=240, freq="h", tz="UTC")
        fifteen_index = pd.date_range("2026-10-20", periods=300, freq="15min", tz="UTC")
        self.intelligence = {
            "data": {
                "intraday": payload(hourly_index),
                "intraday15": payload(fifteen_index, start=12.0, step=0.005),
            }
        }

    def test_overview_table_has_broad_frames(self):
        table = format_timeframe_table(self.daily, self.intelligence)
        for label in ("4 ساعات", "يومي", "أسبوعي", "شهري"):
            self.assertIn(label, table)
        self.assertNotIn("غير متوفر", table)
        self.assertNotIn("15 دقيقة", table)

    def test_opportunity_table_has_trading_frames(self):
        table = format_timeframe_table(
            self.daily,
            self.intelligence,
            opportunity=True,
        )
        for label in ("15 دقيقة", "ساعة", "4 ساعات", "يومي"):
            self.assertIn(label, table)
        self.assertNotIn("أسبوعي", table)
        self.assertNotIn("غير متوفر", table)


if __name__ == "__main__":
    unittest.main()
