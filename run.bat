@echo off
setlocal
cd /d "%~dp0" || exit /b 1

rem Launch without a console window: prefer pythonw, then the py launcher.
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw app_volume.pyw
    goto :done
)

py -3 --version >nul 2>nul
if %errorlevel%==0 (
    start "" py -3w app_volume.pyw
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python app_volume.pyw
    goto :done
)

echo.
echo   Python was not found on this system.
echo.
echo   1. Install Python 3.9 or newer: https://www.python.org/downloads/
echo      Make sure "Add python.exe to PATH" is checked during setup.
echo   2. Then install the only dependency:  pip install pycaw
echo.
pause
exit /b 1

:done
endlocal
