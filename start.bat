@echo off
cd /d C:\Users\Administrator\Desktop\AutoTraderClaude
call venv\Scripts\activate.bat
echo AutoTrader Claude starting...
python main.py %*
pause
