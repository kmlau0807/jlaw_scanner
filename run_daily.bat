@echo off
setlocal
cd /d "C:\Users\lauki\WorkBuddy\2026-08-25-14-58-48\jlaw_scanner"
echo [%date% %time%] === JLaw daily scan START === >> run_daily.log
"C:\Users\lauki\.workbuddy\binaries\python\envs\default\Scripts\python.exe" sched_run.py >> run_daily.log 2>&1
echo [%date% %time%] === JLaw daily scan EXIT code=%errorlevel% === >> run_daily.log
endlocal
