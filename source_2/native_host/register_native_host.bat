@echo off
echo ====================================================
echo   Registering Project Sera Native Messaging Host...
echo ====================================================

SET JSON_PATH=%~dp0com.amanassociates.sera.json
SET JSON_FIREFOX_PATH=%~dp0com.amanassociates.sera.firefox.json
SET HOST_BAT=%~dp0host.bat

python -c "import json, sys; p=r'%JSON_PATH%'; hb=r'%HOST_BAT%'; d=json.load(open(p)); d['path']=hb; json.dump(d, open(p, 'w'), indent=2)" 2>nul
python -c "import json, sys; p=r'%JSON_FIREFOX_PATH%'; hb=r'%HOST_BAT%'; d=json.load(open(p)); d['path']=hb; json.dump(d, open(p, 'w'), indent=2)" 2>nul

REG ADD "HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_PATH%" /f
REG ADD "HKEY_CURRENT_USER\Software\Mozilla\NativeMessagingHosts\com.amanassociates.sera" /ve /t REG_SZ /d "%JSON_FIREFOX_PATH%" /f

echo.
echo SUCCESS! Native messaging host registered for Chrome, Edge, and Mozilla Firefox.
if /I not "%~1"=="--silent" pause
