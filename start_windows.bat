@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
if errorlevel 1 goto error
.venv\Scripts\python.exe app.py
pause
exit /b
:error
echo Installation failed. Check Python and your internet connection.
pause
