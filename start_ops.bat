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

echo [MediaCrawler] Opening ops page: %URL%
start "" "%URL%"

echo [MediaCrawler] Starting API server on port %PORT% ...
"%PY%" -m uvicorn api.main:app --host 127.0.0.1 --port %PORT%

endlocal
