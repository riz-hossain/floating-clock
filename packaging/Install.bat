@echo off
setlocal
title Floating Clock  -  Setup
color 0D
echo.
echo    ==========================================
echo      Floating Clock  -  Setup
echo    ==========================================
echo.
echo    Installs for the current user only.
echo    No administrator rights are needed.
echo.
set "DESKTOP="
set "STARTUP="
set /p "ans=   Create a desktop shortcut? [y/N] "
if /i "%ans%"=="y" set "DESKTOP=-Desktop"
set /p "ans=   Start the clock when Windows starts? [y/N] "
if /i "%ans%"=="y" set "STARTUP=-Startup"
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %DESKTOP% %STARTUP%
if errorlevel 1 (
    echo.
    echo    Setup failed. See the message above.
)
echo.
pause
endlocal
