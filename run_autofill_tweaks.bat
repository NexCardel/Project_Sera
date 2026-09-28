@echo off
REM Autofill tweaks dispatcher: runs work packages one after another until the deadline in
REM docs\autofill_tweaks\autofill-tweaks-plan.json. Restarts itself if it crashes. Close this window
REM to stop; the current WP is retried next time. Run it from the autofill-tweaks worktree (..\APP-autofill).
cd /d "%~dp0"
title Autofill tweaks dispatcher
:again
..\APP\venv\Scripts\python.exe tools\autofill_tweaks.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
