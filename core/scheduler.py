"""
Scheduler — runs hourly scan, FTMO check every 15 min, Sunday review.
Uses APScheduler so it works without Windows Task Scheduler.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger


class SystemScheduler:
    def __init__(self, on_hourly_scan=None, on_ftmo_check=None, on_sunday_review=None):
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self._scan    = on_hourly_scan
        self._ftmo    = on_ftmo_check
        self._sunday  = on_sunday_review

    def start(self):
        if self._scan:
            self.scheduler.add_job(self._safe(self._scan), "interval",
                                   hours=1, id="hourly_scan")
        if self._ftmo:
            self.scheduler.add_job(self._safe(self._ftmo), "interval",
                                   minutes=15, id="ftmo_check")
        if self._sunday:
            self.scheduler.add_job(self._safe(self._sunday), "cron",
                                   day_of_week="sun", hour=22, minute=0,
                                   id="sunday_review")
        self.scheduler.start()
        logger.info("Scheduler started (hourly scan, 15-min FTMO, Sunday review)")

    def stop(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")

    def _safe(self, fn):
        def wrapper():
            try:
                fn()
            except Exception as e:
                logger.error(f"Scheduler job {fn.__name__} failed: {e}")
        wrapper.__name__ = getattr(fn, "__name__", "job")
        return wrapper
