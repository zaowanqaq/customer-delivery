@echo off
setlocal

cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "PORT=8081"
set "URL=http://127.0.0.1:%PORT%/ops-config"

echo [MediaCrawler] Working directory: %CD%

if not exist "%PY%" (
  echo [MediaCrawler] .venv not found, creating virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo [MediaCrawler] Failed to create .venv. Please ensure Python is installed.
    pause
    exit /b 1
  )
)

echo [MediaCrawler] Checking required packages...
"%PY%" -c "import fastapi, uvicorn" >nul 2>nul
if errorlevel 1 (
  echo [MediaCrawler] Installing dependencies...
  "%PY%" -m pip install --upgrade pip
  "%PY%" -m pip install -r requirements.txt
  "%PY%" -m pip install uvicorn
)

echo [MediaCrawler] Checking Playwright browser...
"%PY%" -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); path=p.chromium.executable_path; p.stop(); raise SystemExit(0 if Path(path).exists() else 1)" >nul 2>nul
if errorlevel 1 (
  echo [MediaCrawler] Installing Playwright Chromium browser...
  "%PY%" -m playwright install chromium
)

where lark-cli >nul 2>nul
if errorlevel 1 (
  where lark-cli.cmd >nul 2>nul
)
if errorlevel 1 (
  echo [MediaCrawler] WARNING: lark-cli not found. Feishu Base initialization and sync require lark-cli installed and authorized.
) else (
  echo [MediaCrawler] lark-cli detected. Please make sure it has been authorized with the customer's Feishu account.
)

echo [MediaCrawler] Opening ops page: %URL%
start "" "%URL%"

echo [MediaCrawler] Starting API server on port %PORT% ...
"%PY%" -m uvicorn api.main:app --host 127.0.0.1 --port %PORT%

endlocal
