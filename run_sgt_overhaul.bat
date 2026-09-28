@echo off
REM SGT overhaul dispatcher: runs work packages one after another until the deadline in docs\sgt-overhaul-plan.json.
REM Restarts itself if it crashes. Close this window to stop; the current WP is retried next time.
cd /d "%~dp0"
title SGT overhaul dispatcher
:again
..\APP\venv\Scripts\python.exe tools\sgt_overhaul.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
