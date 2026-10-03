@echo off
REM Sera Distill (SDIS) dispatcher: runs work packages one after another until the deadline in
REM docs\sdis\sdis-plan.json. Restarts itself if it crashes. Close this window to stop; the current
REM WP is retried next time. Run it from the sdis worktree (..\APP-sdis).
cd /d "%~dp0"
title Sera Distill dispatcher
set PYTHONIOENCODING=utf-8
:again
..\APP\venv\Scripts\python.exe tools\sdis.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
