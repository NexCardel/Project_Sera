@echo off
REM UI overhaul dispatcher: runs work packages one after another until the deadline in
REM docs\ui_overhaul\ui-overhaul-plan.json. Restarts itself if it crashes. Close this window
REM to stop; the current WP is retried next time. Run it from the ui-overhaul worktree (..\APP-ui).
cd /d "%~dp0"
title UI overhaul dispatcher
:again
..\APP\venv\Scripts\python.exe tools\ui_overhaul.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
