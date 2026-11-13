@echo off
REM Starts Project Sera using the local virtual environment.
cd /d "%~dp0"
if not exist "venv\Scripts\pythonw.exe" (
  echo venv not found at %~dp0venv
  pause
  exit /b 1
)
start "" "venv\Scripts\pythonw.exe" main.py %*
