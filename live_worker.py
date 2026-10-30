"""Saudi AI Sniper V5 - Continuous scan worker.
Read-only alerts. Historical scanning until live strategy integration.
"""
import logging
import os
import time
from datetime import datetime, time as clock_time
from zoneinfo import ZoneInfo

from telegram_scan import main as scan_and_alert

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ai_sniper")
RIYADH = ZoneInfo("Asia/Riyadh")
INTERVAL = max(300, int(os.getenv("SCAN_INTERVAL_SECONDS", "1800")))


def market_open(now):
    # Saudi market: Sunday to Thursday, 10:00-15:00.
    # This excludes auction phases and market holidays.
    return (
        now.weekday() in (6, 0, 1, 2, 3)
        and clock_time(10, 0) <= now.time() < clock_time(15, 0)
    )


def main():
    log.info("Saudi AI Sniper worker started")
    last_scan = 0.0

    while True:
        now = datetime.now(RIYADH)
        if market_open(now):
            if time.monotonic() - last_scan >= INTERVAL:
                last_scan = time.monotonic()
                try:
                    log.info("Starting scheduled market scan")
                    scan_and_alert()
                    log.info("Scan finished")
                except Exception:
                    log.exception("Scan failed")
        time.sleep(30)


if __name__ == "__main__":
    main()
