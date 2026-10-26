@echo off
setlocal enabledelayedexpansion
title Universal Exhibitor Agent - Setup
color 0A

echo =====================================================================
echo        Universal Exhibitor Agent - Production Setup
echo =====================================================================
echo.
echo [1/4] Checking Python installation...

rem Check if Python is available
where python >nul 2>nul
if %errorlevel% neq 0 (
    where py >nul 2>nul
    if !errorlevel! neq 0 (
        color 0C
        echo.
        echo [ERROR] Python was not found in your system PATH!
        echo Please download and install Python 3.10 or higher from:
        echo https://www.python.org/downloads/
        echo Make sure to check "Add Python to PATH" during installation.
        echo.
        pause
        exit /b 1
    ) else (
        set "PY_CMD=py"
    )
) else (
    set "PY_CMD=python"
)

for /f "tokens=*" %%i in ('%PY_CMD% --version') do set "PY_VER=%%i"
echo   * Found: %PY_VER%
echo.

echo [2/4] Setting up Python virtual environment (.venv)...
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo   * Creating fresh virtual environment...
    %PY_CMD% -m venv .venv
    if !errorlevel! neq 0 (
        color 0C
        echo [ERROR] Failed to create virtual environment!
        pause
        exit /b 1
    )
    echo   * Virtual environment created successfully.
) else (
    echo   * Existing virtual environment detected: .venv
)
echo.

echo [3/4] Installing required dependencies from requirements.txt...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if !errorlevel! neq 0 (
    color 0C
    echo [ERROR] Dependency installation encountered an issue.
    pause
    exit /b 1
)
echo   * Dependencies installed successfully.
echo.

echo [4/4] Installing Playwright Chromium browser binaries...
call ".venv\Scripts\python.exe" -m playwright install chromium
echo   * Playwright browser ready.
echo.

echo =====================================================================
echo                    Setup Completed Successfully!
echo =====================================================================
echo.
echo You can now run the application anytime by double-clicking:
echo   [RUN] run.bat
echo.
echo Press any key to launch the Web Dashboard now, or close this window.
echo =====================================================================
pause
call "%~dp0run.bat"
