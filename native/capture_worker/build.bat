@echo off
REM Builds SeraCaptureWorker.exe (the C# capture worker, core/capture/cs_worker.py) with the C# compiler
REM that ships inside Windows' .NET Framework 4.x - no SDK to install, and the exe runs on any Windows 10/11
REM PC as it is. Output: native\capture_worker\bin\SeraCaptureWorker.exe
setlocal
cd /d "%~dp0"
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (
  echo csc.exe not found under %WINDIR%\Microsoft.NET
  exit /b 1
)
if not exist bin mkdir bin
"%CSC%" /nologo /target:exe /platform:anycpu /optimize+ /warn:4 /out:bin\SeraCaptureWorker.exe ^
  /r:System.Core.dll Uia.cs Json.cs Nodes.cs Scope.cs Backend.cs Reader.cs Flash.cs Program.cs
exit /b %ERRORLEVEL%
