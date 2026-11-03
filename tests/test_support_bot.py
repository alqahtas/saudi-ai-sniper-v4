import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from support_bot import completed_daily, resolve_company


class SupportBotTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"symbol": "2222", "name_ar": "أرامكو السعودية", "name_en": "Saudi Aramco", "short_name": "ARAMCO"},
            {"symbol": "1120", "name_ar": "مصرف الراجحي", "name_en": "Al Rajhi Bank", "short_name": "ALRAJHI"},
            {"symbol": "7010", "name_ar": "اس تي سي", "name_en": "stc", "short_name": "STC"},
        ]

    def test_resolves_symbol_arabic_and_english(self):
        self.assertEqual(resolve_company("2222", self.rows)[0]["symbol"], "2222")
        self.assertEqual(resolve_company("أرامكو", self.rows)[0]["symbol"], "2222")
        self.assertEqual(resolve_company("stc", self.rows)[0]["symbol"], "7010")

    def test_excludes_current_open_market_candle(self):
        tz = ZoneInfo("Asia/Riyadh")
        now = datetime(2026, 11, 3, 11, 0, tzinfo=tz)
        data = pd.DataFrame({"Close": [1, 2]}, index=pd.to_datetime(["2026-11-02", "2026-11-03"]))
        self.assertEqual(len(completed_daily(data, now)), 1)


if __name__ == "__main__":
    unittest.main()
