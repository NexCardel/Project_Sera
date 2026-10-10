@echo off
REM Live view of what the current UI overhaul worker is doing. Close the window to stop watching.
cd /d "%~dp0..\.."
title UI overhaul - live worker
..\APP\venv\Scripts\python.exe Mockups\overhaul\ui_overhaul.py watch
pause
