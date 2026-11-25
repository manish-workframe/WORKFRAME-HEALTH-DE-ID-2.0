@echo off
cd /d "%~dp0"
echo Setting up Workframe Health De-ID (needs internet once)...
where python >nul 2>nul || (echo Python is not installed. Install Python 3.11 or newer from python.org, tick "Add to PATH", then run this again. & pause & exit /b 1)
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Running self-test...
python selftest.py
echo.
echo Setup finished. Start the app with start_windows.bat
pause
