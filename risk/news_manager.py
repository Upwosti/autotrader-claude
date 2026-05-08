"""
News Manager — blocks trading during high-impact news events.
Uses a simple hardcoded schedule + optional ForexFactory scrape.
"""

from datetime import datetime, timezone
from loguru import logger


# High-impact recurring events (weekday, UTC hour range) — simplified
RECURRING_BLOCKS = [
    # (weekday 0=Mon, hour_start, hour_end, description)
    (2, 12, 14, "US CPI Wednesday"),
    (3, 12, 14, "US Initial Claims Thursday"),
    (4, 12, 14, "US NFP Friday"),
]


class NewsManager:
    def __init__(self):
        self.manual_blocks: list = []   # [(start_dt, end_dt, label)]

    def add_block(self, start: datetime, end: datetime, label: str = ""):
        self.manual_blocks.append((start, end, label))
        logger.info(f"News block added: {label} {start} -> {end}")

    def is_safe_to_trade(self, dt: datetime = None) -> bool:
        """Returns True if no news block is active at dt (defaults to now UTC)."""
        if dt is None:
            dt = datetime.now(timezone.utc)

        for start, end, label in self.manual_blocks:
            if start <= dt <= end:
                logger.warning(f"News block active: {label}")
                return False

        weekday = dt.weekday()
        hour    = dt.hour
        for wday, h_start, h_end, desc in RECURRING_BLOCKS:
            if weekday == wday and h_start <= hour < h_end:
                logger.warning(f"Recurring news block: {desc}")
                return False

        return True

    def status(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "safe":         self.is_safe_to_trade(now),
            "manual_count": len(self.manual_blocks),
        }
