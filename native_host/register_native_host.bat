@echo off
echo ====================================================
echo   Registering Project Sera Native Messaging Host...
echo ====================================================

SET JSON_PATH=%~dp0com.amanassociates.sera.json
SET JSON_FIREFOX_PATH=%~dp0com.amanassociates.sera.firefox.json
SET HOST_BAT=%~dp0host.bat

powershell -NoProfile -Command "$j = Get-Content -Raw '%JSON_PATH%' | ConvertFrom-Json; $j.path = '%HOST_BAT%'.Replace('\', '\\'); $j | ConvertTo-Json -Depth 10 | Set-Content '%JSON_PATH%'" 2>nul
powershell -NoProfile -Command "$j = Get-Content -Raw '%JSON_FIREFOX_PATH%' | ConvertFrom-Json; $j.path = '%HOST_BAT%'.Replace('\', '\\'); $j | ConvertTo-Json -Depth 10 | Set-Content '%JSON_FIREFOX_PATH%'" 2>nul

REG ADD "HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\Mozilla\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_FIREFOX_PATH%" /f

echo.
echo SUCCESS! Native messaging host registered for Chrome, Edge, Brave, and Mozilla Firefox.
if /I not "%~1"=="--silent" pause
