@echo off
set "PYTHON=C:\Users\lauki\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo [ERROR] Managed venv python not found at:
  echo   %PYTHON%
  echo.
  echo Fix: install deps into your own Python:
  echo   pip install -r "%~dp0requirements.txt"
  echo then edit this file's PYTHON= to point at your python.exe
  exit /b 1
)
"%PYTHON%" "%~dp0run.py" %*
