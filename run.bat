@echo off
cd /d "%~dp0"

echo ================================================
echo   Telegram Auto-Sender - Launching...
echo ================================================
echo.

set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHON_EXE="

if exist "%PROJECT_PYTHON%" (
    set "PYTHON_EXE=%PROJECT_PYTHON%"
    echo [OK] Using project virtual environment
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] 未找到 Python。请先执行：uv sync
        pause
        exit /b 1
    )
    set "PYTHON_EXE=python"
    echo [--] 项目虚拟环境不存在，使用系统 Python
)

echo [>>] Starting Web UI (http://127.0.0.1:5000)...
echo.

start http://127.0.0.1:5000

"%PYTHON_EXE%" main.py

if errorlevel 1 (
    echo.
    echo ================================================
    echo   ERROR - Check messages above
    echo ================================================
    pause
)
