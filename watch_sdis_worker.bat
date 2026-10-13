@echo off
REM Live view of what the current Sera Distill worker is doing. Close the window to stop watching.
cd /d "%~dp0"
title Sera Distill - live worker
set PYTHONIOENCODING=utf-8
..\APP\venv\Scripts\python.exe tools\sdis.py watch
pause
