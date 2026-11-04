"""Telegram delivery helpers for owner and public channel notifications."""
import logging
import os
import time

import requests

LOG = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096
# Numeric IDs are routing identifiers, not credentials. This private channel ID
# was verified through the bot's my_chat_member update after admin approval.
DEFAULT_CHANNEL_ID = "-1003354695969"


def split_text(text, limit=4000):
    """Split text without breaking Telegram's 4096 character limit."""
    text = str(text or "")
    if not text:
        return [""]
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit + 1)
        if cut < max(1, limit // 2):
            cut = text.rfind(" ", 0, limit + 1)
        if cut < 1:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text or not chunks:
        chunks.append(text)
    return chunks


def notification_targets():
    """Return de-duplicated owner and optional channel targets."""
    values = (
        os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        (os.getenv("TELEGRAM_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID),
    )
    return list(dict.fromkeys(value for value in values if value))


def send_message(token, chat_id, text, *, session=requests, sleep=time.sleep):
    """Send all chunks. Retry one 429 response and isolate no other secrets."""
    if not token or not str(chat_id).strip():
        raise ValueError("Telegram token and chat target are required")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_text(text):
        for attempt in range(2):
            response = session.post(
                url,
                json={"chat_id": str(chat_id), "text": chunk},
                timeout=30,
            )
            if response.status_code == 429 and attempt == 0:
                try:
                    delay = float(
                        (response.json().get("parameters") or {}).get("retry_after", 1)
                    )
                except (TypeError, ValueError, AttributeError):
                    delay = 1.0
                sleep(max(0.0, min(delay, 60.0)))
                continue
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError("Telegram rejected message")
            break
    return True


def broadcast(text, *, token=None, targets=None):
    """Send a notification to the owner and the public channel independently."""
    token = (token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    targets = list(targets if targets is not None else notification_targets())
    if not token or not targets:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or Telegram notification target")
    result = {}
    for target in targets:
        try:
            send_message(token, target, text)
            result[str(target)] = True
            LOG.info("Telegram delivery OK target=%s", target)
        except Exception as exc:
            LOG.error("Telegram delivery failed target=%s error=%s", target, type(exc).__name__)
            result[str(target)] = False
    if not any(result.values()):
        raise RuntimeError("Telegram delivery failed for every configured target")
    return result

