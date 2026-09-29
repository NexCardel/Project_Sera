@echo off
REM Live view of what the current Autofill tweaks worker is doing. Close the window to stop watching.
cd /d "%~dp0"
title Autofill tweaks - live worker
..\APP\venv\Scripts\python.exe tools\autofill_tweaks.py watch
pause
