@echo off
setlocal
title Universal Exhibitor Agent - Web Dashboard
color 0B

echo =====================================================================
echo              Universal Exhibitor Agent - Web Interface
echo =====================================================================
echo.

cd /d "%~dp0"

rem Check if virtual environment exists
if not exist ".venv\Scripts\python.exe" (
    color 0E
    echo [NOTICE] Virtual environment was not found.
    echo Running automated setup first...
    echo.
    call "%~dp0setup.bat"
    exit /b
)

rem Set PYTHONPATH to project root directory
set "PYTHONPATH=%~dp0"

echo Launching agent dashboard and opening default browser...
echo.

rem Run launcher script
call ".venv\Scripts\python.exe" "%~dp0run_ui.py"

if errorlevel 1 (
    color 0C
    echo.
    echo [ERROR] The server exited with an error code.
    pause
)
