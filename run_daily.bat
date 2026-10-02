@echo off
setlocal
cd /d "C:\Users\lauki\WorkBuddy\2026-08-25-14-58-48\jlaw_scanner"

REM --- daily log rotation: archive yesterday's run before starting fresh ---
set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%
if exist run_daily.log (
    if not exist run_daily_%STAMP%.log (
        move /Y run_daily.log run_daily_%STAMP%.log >nul 2>&1
    ) else (
        REM same-day second run: keep both by appending a time suffix
        set TSTAMP=%time:~0,2%%time:~3,2%%time:~6,2%
        move /Y run_daily.log run_daily_%STAMP%_%TSTAMP%.log >nul 2>&1
    )
)

echo [%date% %time%] === JLaw daily scan START === >> run_daily.log
"C:\Users\lauki\.workbuddy\binaries\python\envs\default\Scripts\python.exe" sched_run.py >> run_daily.log 2>&1
echo [%date% %time%] === JLaw daily scan EXIT code=%errorlevel% === >> run_daily.log
endlocal
