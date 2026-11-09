@echo off
REM SDIS pre-dev views: one window for render / groups / arrangement / outline / shape map / tables.
REM Pick a capture or comp page from output\, tick the views, press Make views.
cd /d "%~dp0..\..\.."
set PYTHONIOENCODING=utf-8
if exist venv\Scripts\pythonw.exe (
  start "" venv\Scripts\pythonw.exe tools\pre_dev\class_diff\views_ui.py
) else (
  start "" pythonw tools\pre_dev\class_diff\views_ui.py
)
