@echo off
REM AutoTrader Claude — Watchdog Launcher
REM Starts the watchdog silently in the background using pythonw (no console window)
REM The watchdog monitors run_forever.py and auto-restarts it on crash.

cd /d "%~dp0"

echo Starting AutoTrader Claude Watchdog...
echo Logs: %~dp0logs\watchdog.log

REM Use pythonw.exe so no console window appears
start "" /b "%~dp0venv\Scripts\pythonw.exe" "%~dp0watchdog.py" --pairs XAUUSD,GBPUSD,EURUSD,BTCUSD --hours 0

echo Watchdog launched in background (no console window).
echo To stop: open Task Manager and end pythonw.exe processes.
echo To view logs: type logs\watchdog.log
pause
