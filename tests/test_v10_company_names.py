import unittest
import v10_live_monitor as v10

class CompanyNameAlertTests(unittest.TestCase):
    def test_prefers_arabic_company_name(self):
        self.assertEqual(v10.company_display_name("1120", {"name_ar":"مصرف الراجحي","name_en":"Al Rajhi"}), "مصرف الراجحي")

    def test_uses_company_name_fallback(self):
        self.assertEqual(v10.company_display_name("2222", {"اسم_الشركة":"أرامكو السعودية"}), "أرامكو السعودية")

    def test_warns_with_symbol_if_name_unavailable(self):
        self.assertEqual(v10.company_display_name("2222", {}), "2222")

    def test_support_alert_includes_company(self):
        stock={"name_ar":"شركة اختبار","support_zones":[{"bottom":10,"top":10.1}]}
        events=v10.evaluate("1234",stock,{"price":10.03})
        self.assertIn("الشركة: شركة اختبار (1234)", events[0]["message"])

    def test_break_alert_includes_company(self):
        stock={"name_ar":"شركة اختبار","support_zones":[{"bottom":10,"top":10.1}]}
        events=v10.evaluate("1234",stock,{"price":9.9})
        self.assertIn("الشركة: شركة اختبار (1234)", events[0]["message"])

    def test_breakout_includes_company(self):
        stock={"name_ar":"شركة اختبار","resistance":10,"uptrend":True,"active_strategies":["breakout"]}
        events=v10.evaluate("1234",stock,{"price":10.2})
        self.assertIn("الشركة: شركة اختبار (1234)",events[0]["message"])

if __name__=="__main__":
    unittest.main()
