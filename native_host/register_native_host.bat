@echo off
echo ====================================================
echo   Registering Project Sera Native Messaging Host...
echo ====================================================

SET JSON_PATH=%~dp0com.amanassociates.sera.json

REG ADD "HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f

echo.
echo SUCCESS! Native messaging host registered for Chrome and Edge.
if /I not "%~1"=="--silent" pause
