@echo off
title AutoTrader OMEGA Engine
cd /d C:\Users\Administrator\Desktop\AutoTraderClaude
echo ============================================
echo  AutoTrader OMEGA — Clean Rebuild v3.0
echo  Starting Watchdog (foreground mode)
echo ============================================
call venv\Scripts\activate.bat
python watchdog.py
pause
