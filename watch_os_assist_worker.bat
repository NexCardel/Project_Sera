@echo off
REM Live view of what the current OS Assist worker is doing. Close the window to stop watching.
cd /d "%~dp0"
title OS Assist - live worker
..\APP\venv\Scripts\python.exe tools\os_assist.py watch
pause
