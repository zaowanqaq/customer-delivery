#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY=".venv/bin/python"
PORT="${PORT:-8081}"
URL="http://127.0.0.1:${PORT}/ops-config"
REQUIRED_IMPORTS="import fastapi, uvicorn, playwright, pandas, openpyxl, websockets, xhshow, cv2"

echo "[MediaCrawler] Working directory: $(pwd)"

echo "[MediaCrawler] Checking prerequisites..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "[MediaCrawler] ERROR: python3 not found. Please install Python 3.11 or later."
  echo "  macOS: brew install python@3.11  or  download from https://www.python.org/downloads/"
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  echo "[MediaCrawler] ERROR: Python ${PY_VERSION} found, but 3.11+ is required."
  echo "  Please install Python 3.11 or later."
  exit 1
fi
echo "[MediaCrawler] Python ${PY_VERSION} detected."

if [ "$(uname)" = "Darwin" ]; then
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "[MediaCrawler] WARNING: Xcode Command Line Tools not found."
    echo "  Some packages (e.g. opencv-python) may fail to install."
    echo "  Run: xcode-select --install"
  fi
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[MediaCrawler] WARNING: Node.js not found. pyexecjs requires a JS runtime."
  echo "  Install Node.js from https://nodejs.org/ or: brew install node"
fi

BROWSER_FOUND=false
if [ "$(uname)" = "Darwin" ]; then
  for b in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"; do
    if [ -x "$b" ]; then
      BROWSER_FOUND=true
      echo "[MediaCrawler] Browser found: $b"
      break
    fi
  done
elif [ "$(uname)" = "Linux" ]; then
  for b in google-chrome chromium-browser microsoft-edge; do
    if command -v "$b" >/dev/null 2>&1; then
      BROWSER_FOUND=true
      echo "[MediaCrawler] Browser found: $(command -v "$b")"
      break
    fi
  done
fi
if [ "$BROWSER_FOUND" = "false" ]; then
  echo "[MediaCrawler] WARNING: No Chrome/Edge browser detected. CDP mode requires a browser."
  echo "  Please install Chrome or Edge, or set CUSTOM_BROWSER_PATH in config."
fi

if [ ! -x "$PY" ]; then
  echo "[MediaCrawler] .venv not found, creating virtual environment..."
  python3 -m venv .venv
fi

echo "[MediaCrawler] Checking required packages..."
if ! "$PY" -c "$REQUIRED_IMPORTS" >/dev/null 2>&1; then
  echo "[MediaCrawler] Installing dependencies..."
  PIP_INDEX_ARGS=()
  if [ -n "${PYPI_INDEX_URL:-}" ]; then
    PIP_INDEX_ARGS=(-i "$PYPI_INDEX_URL")
  elif [ -n "${PIP_INDEX_URL:-}" ]; then
    PIP_INDEX_ARGS=(-i "$PIP_INDEX_URL")
  fi
  "$PY" -m pip install "${PIP_INDEX_ARGS[@]}" --upgrade pip
  "$PY" -m pip install "${PIP_INDEX_ARGS[@]}" -r requirements.txt
fi

echo "[MediaCrawler] Checking Playwright browser..."
if ! "$PY" -c "from pathlib import Path; from playwright.sync_api import sync_playwright; p=sync_playwright().start(); path=p.chromium.executable_path; p.stop(); raise SystemExit(0 if Path(path).exists() else 1)" >/dev/null 2>&1; then
  echo "[MediaCrawler] Installing Playwright Chromium browser..."
  "$PY" -m playwright install chromium
fi

if command -v lark-cli >/dev/null 2>&1; then
  echo "[MediaCrawler] lark-cli detected. Please make sure it has been authorized with the customer's Feishu account."
else
  echo "[MediaCrawler] WARNING: lark-cli not found. Feishu Base initialization and sync require lark-cli installed and authorized."
  echo "  Install: npm install -g lark-cli"
  echo "  Authorize: lark-cli auth login"
fi

echo "[MediaCrawler] Opening ops page: ${URL}"
if command -v open >/dev/null 2>&1; then
  open "$URL" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" || true
fi

echo "[MediaCrawler] Starting API server on port ${PORT} ..."
exec "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
