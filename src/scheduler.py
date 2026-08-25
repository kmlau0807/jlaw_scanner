"""
scheduler.py
------------
Cross-platform scheduled scan using APScheduler (works on Windows and Linux
with no OS-specific setup). Runs `scan_and_email` on a cron trigger.
"""
from __future__ import annotations
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger("jlaw.sched")


def run_loop(scan_and_email_fn, hour: int, minute: int, tz: str):
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(
        scan_and_email_fn,
        CronTrigger(hour=hour, minute=minute),
        id="daily_scan",
        replace_existing=True,
    )
    logger.info("Scheduler started: daily at %02d:%02d (%s)", hour, minute, tz)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
