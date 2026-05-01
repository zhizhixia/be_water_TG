@echo off
cd /d "%~dp0"

echo ================================================
echo   Telegram Auto-Sender - Launching...
echo ================================================
echo.

where conda >nul 2>&1
if not errorlevel 1 (
    call conda activate host >nul 2>&1
    if not errorlevel 1 (
        echo [OK] Conda env 'host' activated
    ) else (
        echo [--] Using direct Python path
    )
) else (
    echo [--] Using direct Python path
)

echo [>>] Starting GUI...
echo.

D:\anaconda\envs\host\python.exe main.py

if errorlevel 1 (
    echo.
    echo ================================================
    echo   ERROR - Check messages above
    echo ================================================
    pause
)
