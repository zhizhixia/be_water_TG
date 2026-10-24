@echo off
setlocal
pushd "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
  echo [ERROR] uv executable not found. See rewrite/README.md.
  popd
  pause
  exit /b 1
)
uv run --project rewrite --frozen --group runtime python rewrite/main.py %*
set "exitCode=%ERRORLEVEL%"
popd
if not "%exitCode%"=="0" pause
exit /b %exitCode%

