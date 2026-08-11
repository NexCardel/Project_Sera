@echo off
echo ====================================================
echo   Removing Project Sera Native Messaging Host...
echo ====================================================

REG DELETE "HKEY_CURRENT_USER\Software\Google\Chrome\NativeMessagingHosts\com.amanassociates.sera" /f >nul 2>&1
REG DELETE "HKEY_CURRENT_USER\Software\Microsoft\Edge\NativeMessagingHosts\com.amanassociates.sera" /f >nul 2>&1

echo.
echo SUCCESS! Native messaging host removed from Chrome and Edge registry entries.
if /I not "%~1"=="--silent" pause
