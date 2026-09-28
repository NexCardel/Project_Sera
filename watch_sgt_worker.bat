@echo off
REM Live view of what the current SGT overhaul worker is doing. Close the window to stop watching.
cd /d "%~dp0"
title SGT overhaul - live worker
..\APP\venv\Scripts\python.exe tools\sgt_overhaul.py watch
pause
