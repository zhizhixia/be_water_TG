@echo off
cd /d "%~dp0"

echo ================================================
echo   Telegram Auto-Sender - Launching...
echo ================================================
echo.

where conda >nul 2>&1
if not errorlevel 1 (
    call conda activate be_water >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Conda env 'be_water' activated
    ) else (
        echo [--] Using direct Python path
    )
) else (
    echo [--] Using direct Python path
)

echo [>>] Starting Web UI (http://127.0.0.1:5000)...
echo.

start http://127.0.0.1:5000

D:\miniconda3\envs\be_water\python.exe main.py

if errorlevel 1 (
    echo.
    echo ================================================
    echo   ERROR - Check messages above
    echo ================================================
    pause
)
