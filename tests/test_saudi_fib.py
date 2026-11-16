from datetime import timedelta
from decimal import Decimal as D
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from test_saudi_ema_stack import bars,NOW,STAMP
from saudi_fib import evaluate
import v10_live_monitor as monitor


def cash_bars():
    h,m=bars(60),bars(15)
    for f in (h,m):
        f[["Open","High","Low","Close"]]-=1.14
    h.loc[h.index[-35],"High"]=102
    h.loc[h.index[-25],"Low"]=98
    h.loc[h.index[-7],"High"]=102
    m.loc[m.index[-15],"Low"]=98.80
    m.loc[m.index[-8],"Low"]=98.75
    m.loc[m.index[-1],["Open","Close","Low","High"]]=(99.09,99.1,98.84,99.12)
    return h,m


class SaudiFibTests(unittest.TestCase):
    def test_cash_entry_has_fib_levels_and_cost_adjusted_target(self):
        h,m=cash_bars()
        setup=evaluate(h,m,99.1,STAMP,NOW)
        self.assertIsNotNone(setup)
        self.assertEqual(setup.plan.entry,D("98.856"))
        self.assertEqual(setup.plan.targets[0],D("100.472"))
        self.assertGreaterEqual(setup.estimated_net_reward/setup.estimated_net_risk,D("1.5"))

    def test_stale_quotes_costs_and_current_session_gaps_are_rejected(self):
        h,m=cash_bars()
        self.assertIsNone(evaluate(h,m,99.1,NOW-timedelta(minutes=5),NOW))
        self.assertIsNone(evaluate(h,m,99.1,STAMP,NOW,fee_rate=D(".02")))
        self.assertIsNone(evaluate(h,m.drop(m.index[-2]),99.1,STAMP,NOW))

    def test_monitor_delivers_new_profile_through_existing_alert_path(self):
        h,m=cash_bars();client=Mock()
        client.market.return_value={"is_delayed":False,"updated_at":STAMP.isoformat(),
                                   "index_change_percent":.5,"advancing":150,"declining":60}
        client.intraday.side_effect=[h,m]
        q={"symbol":"2222","price":99.1,"timestamp":STAMP.isoformat()}
        with patch.object(monitor,"SahmkProClient",return_value=client), \
             patch.object(monitor,"now_saudi",return_value=NOW.astimezone(monitor.RIYADH)), \
             patch.object(monitor,"now_utc",return_value=NOW), \
             patch.object(monitor,"process_events",return_value=True) as deliver:
            self.assertEqual(monitor.scan_fib_alerts(Mock(),[({"name_ar":"أرامكو"},q)],{}),1)
            event=deliver.call_args.args[0][0]
            self.assertEqual(event["type"],"FIB_H1_M15")
            self.assertIn("78.6",event["message"])
            self.assertIn("100.47",event["message"])
            self.assertNotIn("161.8",event["message"])

    def test_same_level_is_not_delivered_twice(self):
        q={"symbol":"2222","price":99.1,"timestamp":STAMP.isoformat()}
        event={"type":"FIB_H1_M15","level":98.856,"key":"same","message":"fib"}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(monitor,"STATE_FILE",Path(folder)/"state.json"), \
             patch.object(monitor,"EVENTS_FILE",Path(folder)/"events.json"), \
             patch.object(monitor,"now_utc",return_value=NOW), \
             patch.object(monitor,"telegram_send",return_value=True) as send:
            state={}
            self.assertTrue(monitor.process_events([event],state,q))
            self.assertFalse(monitor.process_events([event],state,q))
            self.assertEqual(send.call_count,1)
