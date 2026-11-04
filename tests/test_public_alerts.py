import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import telegram_delivery as delivery
import v10_live_monitor as v10


class PublicAlertsTests(unittest.TestCase):
    def test_blank_actions_secret_still_targets_approved_channel(self):
        with patch.dict(os.environ, {'TELEGRAM_CHAT_ID': 'owner', 'TELEGRAM_CHANNEL_ID': ''}, clear=True):
            self.assertEqual(delivery.notification_targets(), ['owner', delivery.DEFAULT_CHANNEL_ID])

    def test_each_destination_receives_same_notification(self):
        with patch.object(delivery, 'send_message', return_value=True) as send:
            result = delivery.broadcast('watch', token='mock', targets=['owner', 'channel'])
        self.assertEqual(result, {'owner': True, 'channel': True})
        self.assertEqual([c.args[2] for c in send.call_args_list], ['watch', 'watch'])

    def test_public_failure_is_not_counted_as_delivery_success(self):
        with patch.object(v10, 'broadcast', return_value={'owner': True, 'channel': False}):
            self.assertFalse(v10.telegram_send('watch'))

    def test_many_broken_supports_produce_one_nearest_warning(self):
        events = v10.evaluate('4250', {'name_ar': 'جبل عمر', 'support_zones': [
            {'bottom': 16, 'top': 16}, {'bottom': 15.88, 'top': 15.9}]}, {'price': 15.04})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['level'], 15.88)
        self.assertNotIn('16.00', events[0]['message'])
        self.assertIn('غير', events[0]['message'])

    def test_touch_takes_priority_over_distant_broken_historical_level(self):
        events = v10.evaluate('1234', {'support_zones': [
            {'bottom': 16, 'top': 16}, {'bottom': 15, 'top': 15.05}]}, {'price': 15.04})
        self.assertEqual([e['type'] for e in events], ['SUPPORT_TOUCH'])
        self.assertIn('15 دقيقة نهائية', events[0]['message'])

    def test_time_is_riyadh_not_raw_iso(self):
        self.assertEqual(v10.price_time_label('2026-11-04T10:31:07Z'),
                         '04/11/2026 • 13:31:07 بتوقيت الرياض')

    def test_repeated_state_survives_reload_and_new_level_sends(self):
        with tempfile.TemporaryDirectory() as d:
            state_path, events_path = Path(d)/'state.json', Path(d)/'events.json'
            quote = {'symbol': '4250', 'price': 15.04, 'timestamp': '2026-11-04T10:31:07Z'}
            event = {'key': '4250:break:0', 'type': 'SUPPORT_BREAK', 'level': 15.88, 'message': 'watch'}
            clock = datetime(2026, 11, 4, 10, 31, tzinfo=timezone.utc)
            with patch.object(v10, 'STATE_FILE', state_path), patch.object(v10, 'EVENTS_FILE', events_path), patch.object(v10, 'now_utc', return_value=clock) as now, patch.object(v10, 'telegram_send', return_value=True) as send:
                self.assertTrue(v10.process_events([event], {}, quote))
                reloaded = json.loads(state_path.read_text())
                now.return_value = clock + timedelta(hours=1)
                self.assertFalse(v10.process_events([event], reloaded, quote))
                self.assertEqual(send.call_count, 1)
                changed = dict(event, key='4250:break:1', level=15.5)
                self.assertTrue(v10.process_events([changed], reloaded, quote))
                self.assertEqual(send.call_count, 2)

    def test_quote_must_explicitly_be_realtime(self):
        for delayed in [True, None]:
            with self.subTest(delayed=delayed):
                session = Mock()
                session.get.return_value.json.return_value = {'price': 15, 'updated_at': v10.now_utc().isoformat(), 'is_delayed': delayed}
                self.assertEqual(v10.fetch_quote(session, '4250'), (None, 'realtime_not_verified'))

    def test_verified_quote_is_accepted(self):
        session = Mock()
        session.get.return_value.json.return_value = {'price': 15, 'updated_at': v10.now_utc().isoformat(), 'is_delayed': False}
        quote, error = v10.fetch_quote(session, '4250')
        self.assertIsNone(error)
        self.assertEqual(quote['price'], 15)
