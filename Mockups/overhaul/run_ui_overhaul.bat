@echo off
REM UI overhaul dispatcher: runs work packages one after another until the deadline in
REM Mockups\overhaul\ui-overhaul-plan.json. Restarts itself if it crashes. Close this window
REM to stop; the current WP is retried next time. Lives in the ui-overhaul worktree (..\APP-ui).
REM Cloud-only WPs (kind "cloud", e.g. W0-D) are skipped: start those yourself in a cloud session.
cd /d "%~dp0..\.."
title UI overhaul dispatcher
:again
..\APP\venv\Scripts\python.exe Mockups\overhaul\ui_overhaul.py run
if %errorlevel%==1 (
  echo Dispatcher crashed - restarting in 60 s
  timeout /t 60 /nobreak >nul
  goto again
)
pause
