import os
import unittest
from unittest.mock import patch

from telegram_delivery import notification_targets, split_text, send_message


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload or {"ok": True}
    def json(self):
        return self.payload
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(str(self.status_code))


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def post(self, url, json, timeout):
        self.calls.append(json)
        return self.responses.pop(0)


class TelegramTests(unittest.TestCase):
    def test_targets_include_owner_and_channel_once(self):
        with patch.dict(os.environ, {
            "TELEGRAM_CHAT_ID": "1", "TELEGRAM_CHANNEL_ID": "@public"
        }, clear=False):
            self.assertEqual(notification_targets(), ["1", "@public"])

    def test_long_text_is_split(self):
        chunks = split_text("x" * 9000)
        self.assertTrue(all(len(x) <= 4000 for x in chunks))
        self.assertEqual("".join(chunks), "x" * 9000)

    def test_429_retries_once(self):
        session = Session([
            Response(429, {"ok": False, "parameters": {"retry_after": 0}}),
            Response(),
        ])
        send_message("token", "1", "hello", session=session, sleep=lambda _: None)
        self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()
