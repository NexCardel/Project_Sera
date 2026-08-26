@echo off
cd /d "%~dp0"
echo Installing FST Tracer Alpha requirements...
python -m pip install -r requirements.txt
python tracer.py "..\seraRawPayloadDump.txt" "fst_tracer_alpha_report.xlsx" --watch
