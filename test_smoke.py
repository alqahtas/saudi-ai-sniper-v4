"""Offline smoke tests. No SAHMK calls, Telegram messages, or real trades."""
import ast
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent


class SmokeTests(unittest.TestCase):
    def test_python_syntax(self):
        for filename in ("live_worker.py", "market_scanner.py", "sniper_core.py", "sahmk_pro.py", "telegram_scan.py", "support_bot.py"):
            with self.subTest(filename=filename):
                ast.parse((ROOT / filename).read_text(encoding="utf-8"), filename=filename)

    def test_target_progression_and_deduplication(self):
        import live_worker as w
        original = w.active_trades
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(w, "TRADES_STATE", pathlib.Path(folder) / "state.json"), patch.object(w, "notify", return_value=True) as notify:
                w.active_trades = {"1120": {"name": "Test", "entry": 60.0, "stop": 59.1, "target1": 61.2, "target2": 62.4, "status": "active"}}
                try:
                    w.track_targets("1120", 61.2, "unused", "unused")
                    self.assertEqual(w.active_trades["1120"]["status"], "target1")
                    w.track_targets("1120", 61.2, "unused", "unused")
                    self.assertEqual(notify.call_count, 1)
                    w.track_targets("1120", 62.4, "unused", "unused")
                    self.assertNotIn("1120", w.active_trades)
                    self.assertEqual(notify.call_count, 2)
                finally:
                    w.active_trades = original

    def test_stop(self):
        import live_worker as w
        original = w.active_trades
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(w, "TRADES_STATE", pathlib.Path(folder) / "state.json"), patch.object(w, "notify", return_value=True) as notify:
                w.active_trades = {"1120": {"entry": 60.0, "stop": 59.1, "target1": 61.2, "target2": 62.4, "status": "active"}}
                try:
                    w.track_targets("1120", 59.1, "unused", "unused")
                    self.assertNotIn("1120", w.active_trades)
                    self.assertEqual(notify.call_count, 1)
                finally:
                    w.active_trades = original


if __name__ == "__main__":
    unittest.main()
