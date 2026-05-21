# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.routers import crawler
from api.schemas import PgyKolRunRequest


def test_lark_json_arg_uses_relative_temp_file_and_cleans_up():
    payload = {"fields": ["达人昵称"], "rows": [["数字生命卡兹克"]]}

    with crawler._lark_json_arg(payload) as json_arg:
        assert json_arg.startswith("@.\\")
        temp_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:]
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


@pytest.mark.asyncio
async def test_run_lark_cli_preserves_non_utf8_error_text():
    script = "import sys; sys.stderr.buffer.write('错误'.encode('gb18030')); sys.exit(2)"

    with pytest.raises(HTTPException) as exc_info:
        await crawler._run_lark_cli([sys.executable, "-c", script], timeout_sec=10)

    assert "错误" in exc_info.value.detail
