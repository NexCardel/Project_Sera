@echo off
REM OS Assist dispatcher: runs work packages one after another until the deadline in
REM docs\os_assist\os-assist-plan.json. Restarts itself if it crashes. Close this window
REM to stop; the current WP is retried next time. Run it from the os-assist worktree (..\APP-os-assist).
cd /d "%~dp0"
title OS Assist dispatcher
:again
..\APP\venv\Scripts\python.exe tools\os_assist.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
