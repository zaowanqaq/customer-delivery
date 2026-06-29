# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.routers import crawler
from api.schemas import PgyKolRunRequest, PgyLoginRequest


def test_lark_json_arg_uses_relative_temp_file_and_cleans_up():
    payload = {"fields": ["达人昵称"], "rows": [["数字生命卡兹克"]]}

    with crawler._lark_json_arg(payload) as json_arg:
        assert json_arg.startswith("@./") or json_arg.startswith("@.\\")
        temp_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:].replace("\\", "/")
        assert temp_path.exists()
        assert json.loads(temp_path.read_text(encoding="utf-8")) == payload

    assert not temp_path.exists()


@pytest.mark.asyncio
async def test_pgy_run_kol_route_forces_api_only(monkeypatch):
    captured_args = []

    async def fake_run_pgy_automation(args, timeout_sec=240):
        captured_args.extend(args)
        return {"status": "logged_in_or_public", "outputs": {}, "returncode": 0}

    monkeypatch.setattr(crawler, "_run_pgy_automation", fake_run_pgy_automation)

    await crawler.pgy_run_kol(PgyKolRunRequest(nickname="数字生命卡兹克"))

    assert captured_args[:2] == ["run-kol", "--api-only"]


def test_pgy_browser_args_prefers_detected_browser_path(monkeypatch, tmp_path):
    browser_path = tmp_path / "Google Chrome"
    browser_path.write_text("", encoding="utf-8")

    class FakeLauncher:
        def detect_browser_paths(self):
            return [str(browser_path)]

    monkeypatch.setattr(crawler.config, "CUSTOM_BROWSER_PATH", "")
    monkeypatch.setattr(crawler, "BrowserLauncher", lambda: FakeLauncher())

    assert crawler._pgy_browser_args([]) == ["--executable-path", str(browser_path)]


@pytest.mark.asyncio
async def test_pgy_login_keep_open_uses_detected_browser_path(monkeypatch, tmp_path):
    browser_path = tmp_path / "Google Chrome"
    browser_path.write_text("", encoding="utf-8")
    captured_cmd = []

    class FakeLauncher:
        def detect_browser_paths(self):
            return [str(browser_path)]

    class FakeProcess:
        returncode = None

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return FakeProcess()

    monkeypatch.setattr(crawler.config, "CUSTOM_BROWSER_PATH", "")
    monkeypatch.setattr(crawler, "BrowserLauncher", lambda: FakeLauncher())
    monkeypatch.setattr(crawler, "_pgy_cdp_available", lambda: False)
    monkeypatch.setattr(crawler.subprocess, "Popen", fake_popen)
    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(crawler.asyncio, "sleep", fake_sleep)

    await crawler.pgy_login(PgyLoginRequest(keep_open=True, timeout_ms=30000))

    assert "--executable-path" in captured_cmd
    assert str(browser_path) in captured_cmd
    assert "--channel" not in captured_cmd


@pytest.mark.asyncio
async def test_run_lark_cli_preserves_non_utf8_error_text():
    script = "import sys; sys.stderr.buffer.write('错误'.encode('gb18030')); sys.exit(2)"

    with pytest.raises(HTTPException) as exc_info:
        await crawler._run_lark_cli([sys.executable, "-c", script], timeout_sec=10)

    assert "错误" in exc_info.value.detail
