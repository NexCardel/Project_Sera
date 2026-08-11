@echo off
if exist "%~dp0..\..\Amas_Sera.exe" (
    "%~dp0..\..\Amas_Sera.exe" --native-host %* 2> "%~dp0host_error.log"
) else if exist "%~dp0..\Amas_Sera.exe" (
    "%~dp0..\Amas_Sera.exe" --native-host %* 2> "%~dp0host_error.log"
) else if exist "%~dp0..\..\CompanyInfo1.exe" (
    "%~dp0..\..\CompanyInfo1.exe" --native-host %* 2> "%~dp0host_error.log"
) else if exist "%~dp0..\CompanyInfo1.exe" (
    "%~dp0..\CompanyInfo1.exe" --native-host %* 2> "%~dp0host_error.log"
) else if exist "%~dp0..\venv\Scripts\python.exe" (
    "%~dp0..\venv\Scripts\python.exe" -u "%~dp0host.py" %* 2> "%~dp0host_error.log"
) else (
    python -u "%~dp0host.py" %* 2> "%~dp0host_error.log"
)
