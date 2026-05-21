#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PY=".venv/bin/python"
PORT="${PORT:-8081}"
URL="http://127.0.0.1:${PORT}/ops-config"
REQUIRED_IMPORTS="import fastapi, uvicorn, playwright, pandas, openpyxl, websockets, xhshow, cv2"

echo "[MediaCrawler] Working directory: $(pwd)"

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
fi

echo "[MediaCrawler] Opening ops page: ${URL}"
if command -v open >/dev/null 2>&1; then
  open "$URL" || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" || true
fi

echo "[MediaCrawler] Starting API server on port ${PORT} ..."
exec "$PY" -m uvicorn api.main:app --host 127.0.0.1 --port "$PORT"
