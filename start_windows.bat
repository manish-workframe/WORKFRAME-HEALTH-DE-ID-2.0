@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo Workframe Health De-ID is running at http://127.0.0.1:8765  (close this window to stop it)
python app.py
