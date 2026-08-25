@echo off
echo Installing requirements...
pip install -r requirements.txt

echo.
echo =======================================================
echo   PROJECT SERA - LIVE FST PAYLOAD TRACKER
echo =======================================================
echo Watching "..\seraRawPayloadDump.txt" for live incoming payloads...
echo Output will be continuously saved to "payload_report.xlsx"
echo.
echo Press Ctrl+C in this window to stop the tracker.
echo.

python fst_classifier.py "..\seraRawPayloadDump.txt" "payload_report.xlsx" --watch

pause
