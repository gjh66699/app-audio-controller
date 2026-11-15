@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   应用音量控制器 - 轻量版安装
echo   ============================
echo.

rem ---------- 1. locate Python ----------
rem python.exe and pythonw.exe must come from the SAME install, otherwise
rem pip puts pycaw into one interpreter while the shortcut launches another.
rem Prefer the official py launcher; it points at the real Python install.
set "PY="
set "PYW="
for /f "delims=" %%i in ('py -3 -c "import sys,os;d=os.path.dirname(sys.executable);print(os.path.join(d,\"python.exe\"))" 2^>nul') do if not defined PY set "PY=%%i"
if defined PY (
    for %%i in ("!PY!") do set "PYW=%%~dpipythonw.exe"
    if not exist "!PYW!" set "PY="
)
if not defined PY (
    for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
    if defined PY for %%i in ("!PY!") do set "PYW=%%~dpipythonw.exe"
)
if not defined PY (
    echo   [!] 没有找到 Python。
    echo.
    echo       这个轻量版不内嵌 Python，需要先安装：
    echo       https://www.python.org/downloads/
    echo       安装时请勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)
if not exist "!PYW!" (
    echo   [!] 找到了 python.exe 但没找到同目录的 pythonw.exe：
    echo       !PY!
    echo       建议重新安装 Python 并勾选 "Add python.exe to PATH"。
    pause
    exit /b 1
)
echo   Python: !PY!

rem ---------- 2. install pycaw into that same interpreter ----------
"!PY!" -c "import pycaw" >nul 2>nul
if errorlevel 1 (
    echo.
    echo   正在安装依赖 pycaw, 只有几百 KB, 需要联网...
    "!PY!" -m pip install --quiet --disable-pip-version-check pycaw
    if errorlevel 1 (
        echo   [!] pycaw 安装失败，请手动执行：
        echo       "!PY!" -m pip install pycaw
        pause
        exit /b 1
    )
)
echo   依赖: pycaw 已就绪

rem ---------- 3. copy files into the install dir ----------
set "DEST=%LOCALAPPDATA%\Programs\AppVolumeController"
if not exist "%DEST%" mkdir "%DEST%" 2>nul
copy /y "app_volume.pyw" "%DEST%\" >nul
copy /y "app_volume_icon.ico" "%DEST%\" >nul
copy /y "make_shortcut.py" "%DEST%\" >nul
copy /y "run.bat" "%DEST%\" >nul
echo   安装目录: %DEST%

rem ---------- 4. desktop shortcut ----------
rem Written by make_shortcut.py, which emits the .lnk binary directly.
rem Avoids WScript.Shell because COM can be blocked by security policy.
pushd "%DEST%"
"!PY!" make_shortcut.py --force
popd

rem ---------- 5. uninstall helper ----------
>"%DEST%\卸载.bat" echo @echo off
>>"%DEST%\卸载.bat" echo chcp 65001 ^>nul
>>"%DEST%\卸载.bat" echo del "%%USERPROFILE%%\Desktop\应用音量控制器.lnk" 2^>nul
>>"%DEST%\卸载.bat" echo rmdir /s /q "%%~dp0" 2^>nul

echo.
echo   完成。桌面上应该有「应用音量控制器」了。
echo.
echo   卸载：双击安装目录里的 卸载.bat，或者手动删掉：
echo   %DEST%
echo.
pause
