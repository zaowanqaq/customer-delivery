#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY=".venv/bin/python"
PORT="${PORT:-8081}"
URL="http://127.0.0.1:${PORT}/ops-config"

echo "[MediaCrawler] Working directory: $(pwd)"

if [ ! -x "$PY" ]; then
  echo "[MediaCrawler] .venv not found, creating virtual environment..."
  python3 -m venv .venv
fi

echo "[MediaCrawler] Checking required packages..."
if ! "$PY" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "[MediaCrawler] Installing dependencies..."
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt
  "$PY" -m pip install uvicorn
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
fi

echo "[MediaCrawler] Opening ops page: ${URL}"
if command -v open >/dev/null 2>&1; then
  open "$URL" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" || true
fi

echo "[MediaCrawler] Starting API server on port ${PORT} ..."
exec "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
