from copy import deepcopy
from decimal import Decimal as D
import unittest

from fib_h1_m15 import RATIOS, candles, drawing, select_setup
from fib_fixtures import frames,NOW


class HourlyFibTests(unittest.TestCase):
    def test_only_screenshot_levels_are_enabled(self):
        self.assertEqual(RATIOS,tuple(map(D,("0",".236",".382",".786","1"))))

    def test_three_anchors_are_not_a_two_point_target(self):
        prices=[99,100,102,109,103,100,96,91,96,100,110,119,115,114,112,111,110]
        rows=[dict(high=D(p+1),low=D(p-1),close_time=i*3600000+3599999) for i,p in enumerate(prices)]
        result=drawing(rows,"LONG")
        self.assertEqual([p.price for p in result.anchors],list(map(D,(110,90,120))))
        self.assertEqual([p for _,p in result.projections],list(map(D,("120","115.280","112.360","104.280","100"))))
        self.assertEqual([p for _,p in result.retracements],list(map(D,("120","112.920","108.540","96.420","90"))))
        self.assertIsNone(drawing(rows[:13],"LONG"))

    def test_long_entry_and_targets_are_in_profit_direction(self):
        data=frames()
        result,reason=select_setup(data["1h"],data["15m"],NOW)
        self.assertEqual(reason,"approved")
        self.assertEqual((result.entry,result.stop),(D("98.856"),D("98.75")))
        self.assertEqual(result.targets,(D("100.472"),D("101.056"),D("102")))
        self.assertEqual([p.top for p in result.drawing.anchors],[True,False,True])
        self.assertLess(result.drawing.anchors[-1].confirmed_ms,data["15m"][-1]["open_time"])

    def test_short_is_the_exact_mirror(self):
        data=frames(True)
        result,reason=select_setup(data["1h"],data["15m"],NOW)
        self.assertEqual(reason,"approved")
        self.assertEqual((result.entry,result.stop),(D("101.144"),D("101.25")))
        self.assertEqual(result.targets,(D("99.528"),D("98.944"),D("98")))
        self.assertEqual([p.top for p in result.drawing.anchors],[False,True,False])

    def test_a_level_touch_without_15m_rejection_is_not_an_entry(self):
        data=frames();data["15m"][-1]["open"]=99.11
        self.assertEqual(select_setup(data["1h"],data["15m"],NOW)[1],"fib_15m_rejection")

    def test_opposing_ema_stacks_block_entry(self):
        data=frames();data["15m"]=frames(True)["15m"]
        self.assertEqual(select_setup(data["1h"],data["15m"],NOW)[1],"fib_ema_alignment")

    def test_forming_candle_does_not_repaint_a_signal(self):
        data=frames()
        expected=select_setup(data["1h"],data["15m"],NOW)
        for interval,duration in (("1h",3600000),("15m",900000)):
            r=deepcopy(data[interval][-1]);r.update(open_time=NOW,close_time=NOW+duration-1,high=9999)
            data[interval].append(r)
        self.assertEqual(select_setup(data["1h"],data["15m"],NOW),expected)

    def test_hourly_confirmation_at_15m_close_cannot_be_used_retroactively(self):
        data=frames()
        baseline=select_setup(data["1h"],data["15m"],NOW)[0].drawing
        data["1h"][-1]["high"]=1000
        result=select_setup(data["1h"],data["15m"],NOW)[0]
        self.assertEqual(result.drawing,baseline)

    def test_stale_gapped_duplicate_and_nan_data_fail_closed(self):
        for issue in ("stale","gap","duplicate","nan","duration"):
            with self.subTest(issue=issue):
                data=frames();rows=data["15m"]
                if issue=="stale":del rows[-1]
                if issue=="gap":del rows[-2]
                if issue=="duplicate":rows.insert(-1,deepcopy(rows[-2]))
                if issue=="nan":rows[-1]["close"]=float("nan")
                if issue=="duration":rows[-1]["close_time"]-=1
                self.assertIsNone(select_setup(data["1h"],rows,NOW)[0])

    def test_break_of_hourly_anchor_invalidates_setup(self):
        data=frames();data["1h"][-2]["low"]=97
        self.assertIsNone(select_setup(data["1h"],data["15m"],NOW)[0])

    def test_no_trade_without_140_bars(self):
        data=frames()
        self.assertIsNone(select_setup(data["1h"][-139:],data["15m"],NOW)[0])

    def test_existing_resistance_before_first_target_blocks_entry(self):
        data=frames(); data["15m"][-10]["high"]=100
        self.assertIsNone(select_setup(data["1h"],data["15m"],NOW)[0])

    def test_stop_is_beyond_the_rejection_wick(self):
        data=frames(); data["15m"][-1]["low"]=98.74
        setup,_=select_setup(data["1h"],data["15m"],NOW)
        self.assertLess(setup.stop,D("98.74"))
