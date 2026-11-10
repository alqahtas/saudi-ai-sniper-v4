from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import v10_live_monitor as monitor
from saudi_ema_stack import evaluate
from sahmk_intelligence import SahmkProClient

NOW = datetime(2026, 11, 10, 9, 31, tzinfo=timezone.utc)
STAMP = NOW - timedelta(seconds=10)


def bars(minutes):
    dates = pd.date_range("2026-09-01", NOW.date(), tz="Asia/Riyadh")
    timestamps = []
    for day in dates:
        if day.dayofweek in (6, 0, 1, 2, 3):
            timestamps.extend(pd.date_range(day + pd.Timedelta(hours=10), periods=300 // minutes,
                                            freq=f"{minutes}min"))
    index = pd.DatetimeIndex(timestamps)
    index = index[index + pd.Timedelta(minutes=minutes) <= NOW]
    step = .02 if minutes == 60 else .004
    closes = [100.24 - (len(index) - 1 - i) * step for i in range(len(index))]
    frame = pd.DataFrame({"Open": closes, "High": [v+.015 for v in closes],
                          "Low": [v-.015 for v in closes], "Close": closes, "Volume": 100},
                         index=index)
    if minutes == 15:
        frame.loc[index[-15], "Low"] = 99.95
        frame.loc[index[-8], "Low"] = 99.88
        frame.loc[index[-3], ["Open", "Close", "Low"]] = (100.20, 100.20, 100.19)
    return frame


class SaudiEmaTests(unittest.TestCase):
    def setUp(self):
        self.hourly, self.fifteen = bars(60), bars(15)

    def signal(self, price=100.24, quote_stamp=STAMP):
        return evaluate(self.hourly, self.fifteen, price, quote_stamp, NOW)

    def test_uses_hourly_line_and_nearest_confirmed_support(self):
        setup = self.signal()
        self.assertIsNotNone(setup)
        self.assertAlmostEqual(setup.entry, 100, places=2)
        self.assertEqual(setup.stop, 99.95)
        self.assertGreater(setup.strength, 50)
        self.assertEqual(setup.confirmed_at.strftime("%H:%M"), "12:30")

    def test_forming_bar_cannot_change_confirmation(self):
        expected = self.signal()
        next_bar = self.fifteen.index[-1] + pd.Timedelta(minutes=15)
        self.fifteen.loc[next_bar] = [90, 90, 90, 90, 1]
        self.assertEqual(self.signal(), expected)

    def test_gap_stale_quote_and_away_from_entry_fail_closed(self):
        self.assertIsNone(self.signal(price=101))
        self.assertIsNone(self.signal(quote_stamp=NOW - timedelta(minutes=2)))
        self.fifteen = self.fifteen.drop(self.fifteen.index[-2])
        self.assertIsNone(self.signal())

    def test_falling_rsi_blocks_an_aligned_stack(self):
        self.fifteen.loc[self.fifteen.index[-1], ["Open", "Close", "Low"]] = (100.225, 100.225, 100.215)
        self.assertIsNone(self.signal())

    def test_15m_live_request_fits_first_page(self):
        client = SahmkProClient(session=Mock())
        with patch.object(client, "_get") as get:
            client.intraday("2222", "15min", recent=True)
        params = get.call_args.args[1]
        self.assertEqual((pd.Timestamp(params["to"]) - pd.Timestamp(params["from"])).days, 27)
        self.assertEqual(params["limit"], 500)
        self.assertLess(get.call_args.kwargs["ttl"], 900)

    def test_monitor_reads_both_frames_and_delivers_through_existing_path(self):
        client = Mock()
        client.market.return_value = {"is_delayed": False, "updated_at": STAMP.isoformat(),
                                      "index_change_percent": 0.5, "advancing": 150, "declining": 60}
        client.intraday.side_effect = [self.hourly, self.fifteen]
        quote = {"symbol": "2222", "price": 100.24, "timestamp": STAMP.isoformat()}
        state = {}
        with patch.object(monitor, "SahmkProClient", return_value=client), \
             patch.object(monitor, "now_saudi", return_value=NOW.astimezone(monitor.RIYADH)), \
             patch.object(monitor, "now_utc", return_value=NOW), \
             patch.object(monitor, "process_events", return_value=True) as deliver:
            self.assertEqual(monitor.scan_ema_alerts(Mock(), [({"name_ar": "أرامكو"}, quote)], state), 1)
            event = deliver.call_args.args[0][0]
            self.assertEqual(event["type"], "EMA_STACK_RETEST")
            self.assertIn("أرامكو", event["message"])
            self.assertIn("99.95", event["message"])
            self.assertEqual([call.args[1] for call in client.intraday.call_args_list], ["60min", "15min"])
            client.market.return_value["is_delayed"] = True
            deliver.reset_mock()
            self.assertEqual(monitor.scan_ema_alerts(Mock(), [({}, quote)], state), 0)
            deliver.assert_not_called()

    def test_ema_and_legacy_alert_fingerprints_are_independent(self):
        quote = {"symbol": "2222", "price": 100.24, "timestamp": STAMP.isoformat()}
        standard = {"key": "support", "type": "SUPPORT_TOUCH", "level": 100, "message": "watch"}
        ema = {"key": "ema", "type": "EMA_STACK_RETEST", "level": 100, "message": "ema"}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(monitor, "STATE_FILE", Path(folder) / "state.json"), \
             patch.object(monitor, "EVENTS_FILE", Path(folder) / "events.json"), \
             patch.object(monitor, "now_utc", return_value=NOW), \
             patch.object(monitor, "telegram_send", return_value=True) as send:
            state = {}
            self.assertTrue(monitor.process_events([standard, ema], state, quote))
            self.assertFalse(monitor.process_events([standard, ema], state, quote))
            self.assertEqual(send.call_count, 2)
