@echo off
rem ============================================================
rem  VDO Grabber - run the desktop app from Python source
rem  Usage:
rem    run.bat                      launch the app
rem    run.bat --selftest           headless smoke test
rem    run.bat https://example.com  launch and open a URL
rem ============================================================
setlocal
title VDO Grabber (Python)
cd /d "%~dp0"

rem ---- locate Python -----------------------------------------
set "PY_EXE="
where python >nul 2>&1 && set "PY_EXE=python"
if not defined PY_EXE where py >nul 2>&1 && set "PY_EXE=py"
if not defined PY_EXE (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo or use the packaged dist\VDOGrabber.exe instead - no Python needed.
    pause
    exit /b 1
)

rem ---- install dependencies on first run ---------------------
%PY_EXE% -c "import webview" >nul 2>&1
if errorlevel 1 (
    echo [INFO] First run - installing dependencies ^(pywebview, Pillow^)...
    %PY_EXE% -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies. Check your internet connection.
        pause
        exit /b 1
    )
)

rem ---- launch -------------------------------------------------
echo [INFO] Starting VDO Grabber from source...
%PY_EXE% "app\main.py" %*
if errorlevel 1 (
    echo.
    echo [ERROR] The app exited with an error. Check the log:
    echo   %APPDATA%\VDOGrabber\logs\app.log
    pause
)
endlocal
exit /b %errorlevel%
