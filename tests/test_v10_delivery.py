import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import v10_live_monitor as v10

class DeliveryTests(unittest.TestCase):
    def test_failed_telegram_does_not_start_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path=Path(directory)/"state.json"
            event_path=Path(directory)/"events.json"
            quote={"symbol":"2222","price":20,"timestamp":"2026-11-02T10:00:00+00:00"}
            event={"key":"2222:touch:0","type":"SUPPORT_TOUCH","message":"test"}
            state={}
            with patch.object(v10,"STATE_FILE",state_path), patch.object(v10,"EVENTS_FILE",event_path), patch.object(v10,"telegram_send",return_value=False):
                self.assertFalse(v10.process_events([event],state,quote))
            self.assertEqual(state,{})
            self.assertFalse(state_path.exists())
    def test_successful_telegram_records_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path=Path(directory)/"state.json"
            event_path=Path(directory)/"events.json"
            quote={"symbol":"2222","price":20,"timestamp":"2026-11-02T10:00:00+00:00"}
            event={"key":"2222:touch:0","type":"SUPPORT_TOUCH","message":"test"}
            state={}
            with patch.object(v10,"STATE_FILE",state_path), patch.object(v10,"EVENTS_FILE",event_path), patch.object(v10,"telegram_send",return_value=True):
                self.assertTrue(v10.process_events([event],state,quote))
            self.assertIn("2222:touch:0",state)
            self.assertTrue(state_path.exists())
            self.assertTrue(event_path.exists())

if __name__=="__main__":
    unittest.main()
