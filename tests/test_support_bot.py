import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from support_bot import _help, compact_analysis, completed_daily, resolve_company, telegram_message


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


    def test_compact_analysis_keeps_decision_and_hides_raw_pro_detail(self):
        full = """🇸🇦 تحليل سابك (2010)
السعر: 47.44 ريال | التغير: 0.94%
عرض/طلب: 47.42 / 47.44
🎯 الخطة: مضاربة لحظية — جلسة إلى 3 جلسات
ضع أمر LIMIT مقترح عند 47.20 ريال
وقف الخسارة: 46.26 ريال (-2% من الدخول)
الهدف الأول: 49.38 ريال | عائد/مخاطرة 2.31
الهدف الثاني: 49.56 ريال | عائد/مخاطرة 2.50
الدعوم: 47.3، 47.02
المقاومات: 47.76، 48.16
📈 الزخم: محايد | RSI 54.7
💼 ماليًا: مرتفع المخاطر
P/E: غير متوفر | P/B: 0.96
ROE: -16.65% | هامش صافي: -22.12%
الخطة مشروطة بثبات الدعم وظهور ارتداد؛ يُلغى السيناريو عند ضرب الوقف.
تحليل آلي لأغراض معلوماتية وليس توصية استثمارية
📊 بيانات SAHMK Pro
وقت السعر: الآن | لحظي
📚 عمق السوق — أفضل 5 مستويات
طلبات: 47.42×6
عروض: 47.44×8,587
السبريد: 0.02 ريال (0.042%)
نسبة قيمة الطلب/العرض: 0.05
عروض البيع أقوى من الطلبات في أول 5 مستويات.
🧾 آخر التنفيذات: اتجاه الصفقات المنفذة غير متوفر.
⏱️ فريم 60 دقيقة: صاعد | دعم 45.84 | مقاومة 47.90
🇸🇦 تاسي: 9,876.13 | -0.07%
🏦 بيانات مالية ربع سنوية/موسعة
P/E غير متوفر | P/B غير متوفر"""

        compact = compact_analysis(full)

        self.assertIn("ضع أمر LIMIT", compact)
        self.assertIn("أقرب دعم: 47.3", compact)
        self.assertIn("P/B: 0.96", compact)
        self.assertIn("عروض البيع أقوى", compact)
        self.assertIn("فريم 60 دقيقة", compact)
        self.assertNotIn("طلبات:", compact)
        self.assertNotIn("وقت السعر:", compact)
        self.assertNotIn("غير متوفر", compact)
        self.assertLess(len(compact), len(full))

    def test_help_exposes_optional_full_report(self):
        self.assertIn("/details سابك", _help())


    def test_accepts_channel_post_for_public_reply(self):
        update = {
            "update_id": 1,
            "channel_post": {
                "chat": {"id": -1003354695969, "type": "channel"},
                "sender_chat": {"id": -1003354695969},
                "text": "سابك",
            },
        }
        message = telegram_message(update)
        self.assertEqual(message["text"], "سابك")
        self.assertEqual(message["chat"]["id"], -1003354695969)

    def test_excludes_current_open_market_candle(self):
        tz = ZoneInfo("Asia/Riyadh")
        now = datetime(2026, 11, 3, 11, 0, tzinfo=tz)
        data = pd.DataFrame({"Close": [1, 2]}, index=pd.to_datetime(["2026-11-02", "2026-11-03"]))
        self.assertEqual(len(completed_daily(data, now)), 1)


if __name__ == "__main__":
    unittest.main()
