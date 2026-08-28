@echo off
title DOM_Parser_1 — Sera DOM Classifier & Audit Watcher
cd /d "%~dp0"

echo ===================================================
echo   PROJECT SERA: DOM_Parser_1 Watcher
echo   Visual Layer Snapshot & Identity Classifier
echo ===================================================
echo.

python dom_parser.py "..\rawPayload.db" "dom_audit_report.xlsx" --watch

if errorlevel 1 (
    echo.
    echo [!] Python execution encountered an issue.
    pause
)
