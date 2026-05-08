@echo off
cd /d C:\Users\Administrator\Desktop\AutoTraderClaude
call venv\Scripts\activate.bat
echo Dashboard starting at http://144.91.69.63:5000
python main.py dashboard --port 5000
pause
