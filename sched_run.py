"""
sched_run.py — headless launcher for the daily scheduled scan + email.

Used by the Windows Scheduled Task (runs as SYSTEM, no console).
Captures all stdout/stderr to daily_log.txt so a failed run is diagnosable.
"""
from __future__ import annotations
import os
import sys
import io
import time
import datetime
import contextlib
import logging
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("jlaw.sched")

from src.main import load_config, do_scan
from src import universe_builder as ub


def main():
    cfg = load_config()
    root = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(root, "daily_log.txt")
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Best-effort: refresh both universes from live index data before scanning.
    # If a fetch fails, the existing universe is kept and the scan continues.
    refresh_lines = []
    for label, fn in (("HK", ub.refresh_hk_universe),
                      ("US", ub.refresh_us_universe)):
        try:
            stats = fn()
            refresh_lines.append(
                f"UNIVERSE REFRESH ({label}): {stats['n_before']} -> {stats['n_after']} "
                f"(live={stats['live_n']}, added={stats['n_added']})")
        except Exception:  # noqa: BLE001
            refresh_lines.append(f"UNIVERSE REFRESH ({label}): skipped (error)")
            log.warning("%s universe refresh failed", label, exc_info=True)
    refresh_line = "\n".join(refresh_lines)

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            do_scan(cfg, send_email=True)
        text = buf.getvalue()
    except Exception:  # noqa: BLE001
        text = "UNCAUGHT EXCEPTION:\n" + traceback.format_exc()

    # Append the captured run log. Retry a few times because on Windows the
    # file can be momentarily locked by an editor / AV scan the user has open
    # while monitoring. A transient lock must NOT crash the run — the email was
    # already sent above.
    block = f"\n===== {ts} =====\n{refresh_line}\n{text}\n"
    _append_log(log_path, block)


def _append_log(path: str, block: str) -> None:
    last_err = None
    for _ in range(6):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(block)
            return
        except (PermissionError, OSError) as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0)
    # Could not write after retries — surface to stderr but do NOT crash.
    sys.stderr.write(
        f"WARNING: could not write {path} ({last_err}); the email was still sent.\n")


if __name__ == "__main__":
    main()
