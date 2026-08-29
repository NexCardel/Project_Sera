@echo off
if exist "%~dp0..\..\Amas_Sera.exe" (
    "%~dp0..\..\Amas_Sera.exe" --native-host %*
) else if exist "%~dp0..\Amas_Sera.exe" (
    "%~dp0..\Amas_Sera.exe" --native-host %*
) else if exist "%~dp0Amas_Sera.exe" (
    "%~dp0Amas_Sera.exe" --native-host %*
) else (
    python -u "%~dp0host.py" %*
)
