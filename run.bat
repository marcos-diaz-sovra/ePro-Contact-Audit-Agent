@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PY=py -3"
) else (
    set "PY=python"
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
    echo During setup, check "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo Failed to create .venv
        pause
        exit /b 1
    )
)

echo Installing / updating packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Package install failed.
    pause
    exit /b 1
)

echo Installing Chromium for Playwright (first run only)...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
    echo Playwright Chromium install failed.
    pause
    exit /b 1
)

echo Starting ePro Contact Audit Agent...
".venv\Scripts\python.exe" gui.py
if errorlevel 1 pause
