@echo off
:: Self-elevation to Administrator
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo Requesting Administrator privileges to create Firefox distribution policy...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo =========================================================
echo   Setting up Permanent Mozilla Firefox Extension Policy
echo =========================================================
echo.

SET "DIST_DIR=C:\Program Files\Mozilla Firefox\distribution"
SET "POLICY_FILE=%DIST_DIR%\policies.json"
SET "XPI_FILE=C:\Users\Nex\AmanAssociates_Sera\sera_extension_firefox.xpi"

IF NOT EXIST "C:\Program Files\Mozilla Firefox" (
    echo [ERROR] Mozilla Firefox folder not found in C:\Program Files\Mozilla Firefox
    pause
    exit /b 1
)

IF NOT EXIST "%DIST_DIR%" (
    echo Creating directory: "%DIST_DIR%"
    mkdir "%DIST_DIR%"
)

:: Firefox only accepts a Mozilla-signed xpi: build it with  python build_tools/sign_firefox.py
SET "SIGNED_XPI=%~dp0..\..\package_assets\extension\ProjectSeraCompanion.signed.xpi"
IF EXIST "%SIGNED_XPI%" (
    IF NOT EXIST "C:\Users\Nex\AmanAssociates_Sera" mkdir "C:\Users\Nex\AmanAssociates_Sera"
    copy /Y "%SIGNED_XPI%" "%XPI_FILE%" >nul
    echo Copied signed xpi to "%XPI_FILE%"
) ELSE IF NOT EXIST "%XPI_FILE%" (
    echo [ERROR] No signed xpi found. Run: python build_tools/sign_firefox.py
    pause
    exit /b 1
)

echo Writing policies.json...
(
echo {
echo   "policies": {
echo     "ExtensionSettings": {
echo       "sera-companion@amanassociates.com": {
echo         "installation_mode": "normal_installed",
echo         "install_url": "file:///C:/Users/Nex/AmanAssociates_Sera/sera_extension_firefox.xpi"
echo       }
echo     }
echo   }
echo }
) > "%POLICY_FILE%"

echo.
echo =========================================================
echo  SUCCESS! Firefox policy created at:
echo  "%POLICY_FILE%"
echo.
echo  Restart Mozilla Firefox now. The extension will stay
echo  permanently installed on every browser launch!
echo =========================================================
echo.
pause
