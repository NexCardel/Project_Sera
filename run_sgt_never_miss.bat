@echo off
REM SGT never-miss dispatcher: runs work packages one after another until the deadline in
REM docs\sgt_never_miss\sgt-never-miss-plan.json. Restarts itself if it crashes. Close this window
REM to stop; the current WP is retried next time. Runs in the main APP folder, on main.
cd /d "%~dp0"
title SGT never-miss dispatcher
set PYTHONIOENCODING=utf-8
:again
venv\Scripts\python.exe tools\sgt_never_miss.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
