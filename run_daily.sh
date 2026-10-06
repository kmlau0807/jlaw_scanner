#!/usr/bin/env bash
# macOS / Linux daily launcher for jlaw_scanner.
# Equivalent of Windows run_daily.bat — called by launchd (or cron).
#
# Usage:
#   ./run_daily.sh
#
# It rotates yesterday's log, then runs sched_run.py with the project venv
# python (falls back to python3 if no venv exists), appending all output
# to run_daily.log.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

STAMP="$(date +%Y%m%d)"
if [ -f run_daily.log ] && [ ! -f "run_daily_${STAMP}.log" ]; then
    mv -f run_daily.log "run_daily_${STAMP}.log"
fi

# Prefer a project venv; otherwise use system python3.
if [ -x ./venv/bin/python ]; then
    PY=./venv/bin/python
elif [ -x ./venv/bin/python3 ]; then
    PY=./venv/bin/python3
else
    PY=python3
fi

echo "[$(date)] === JLaw daily scan START ===" >> run_daily.log
"$PY" sched_run.py >> run_daily.log 2>&1
echo "[$(date)] === JLaw daily scan EXIT code=$? ===" >> run_daily.log
