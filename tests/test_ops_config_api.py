# -*- coding: utf-8 -*-
import base64
import json
from pathlib import Path

import pytest

from api.main import OPS_CONFIG_DEFAULT, PROJECT_BOUND_FIELDS, OpsConfigPayload
from api.routers import crawler


def test_ops_config_preserves_collaboration_comment_table_fields():
    payload = OpsConfigPayload(
        platform="xhs",
        collab_comments_table_name="合作笔记评论表",
        collab_comments_table_id="tbl_collab_comments",
    )
    data = payload.model_dump()

    assert OPS_CONFIG_DEFAULT["collab_comments_table_name"] == "合作笔记评论表"
    assert OPS_CONFIG_DEFAULT["collab_comments_table_id"] == ""
    assert data["collab_comments_table_name"] == "合作笔记评论表"
    assert data["collab_comments_table_id"] == "tbl_collab_comments"
    assert "collab_comments_table_id" in PROJECT_BOUND_FIELDS


def test_ops_config_defaults_to_cookie_login_only():
    payload = OpsConfigPayload(platform="xhs")

    assert OPS_CONFIG_DEFAULT["login_type"] == "cookie"
    assert payload.login_type == "cookie"


def test_extract_base_name_from_base_get_payload():
    payload = {"data": {"app": {"name": "个人小红书测试"}}}

    assert crawler._extract_base_name(payload) == "个人小红书测试"


@pytest.mark.asyncio
async def test_base_info_endpoint_reads_base_name(monkeypatch):
    captured_cmd = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        captured_cmd.extend(cmd)
        return {"data": {"app": {"name": "个人小红书测试"}}}

    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")

    result = await crawler.get_base_info("https://my.feishu.cn/base/app123?table=tbl456")

    assert result["status"] == "ok"
    assert result["base_token"] == "app123"
    assert result["name"] == "个人小红书测试"
    assert "+base-get" in captured_cmd
    assert "app123" in captured_cmd


@pytest.mark.asyncio
async def test_base_info_endpoint_soft_fails_when_name_lookup_is_unavailable(monkeypatch):
    async def fake_get_base_info(_token):
        raise crawler.HTTPException(status_code=404, detail="Not Found")

    monkeypatch.setattr(crawler, "_get_base_info", fake_get_base_info)

    result = await crawler.get_base_info("https://my.feishu.cn/base/app123?table=tbl456")

    assert result == {"status": "ok", "base_token": "app123", "name": "", "warning": "Not Found"}


def test_parse_sample_account_txt_file_dedupes_and_ignores_headers():
    content = "账号ID\nabc_123\nhttps://www.xiaohongshu.com/user/profile/abc\nabc_123\n".encode("utf-8")

    accounts = crawler._parse_sample_account_file("accounts.txt", content)

    assert accounts == ["abc_123", "https://www.xiaohongshu.com/user/profile/abc"]


@pytest.mark.asyncio
async def test_import_sample_accounts_endpoint_accepts_base64_txt():
    content = base64.b64encode("账号ID\nabc_123\nxhs-user-99\n".encode("utf-8")).decode("ascii")

    result = await crawler.import_sample_accounts(
        crawler.SampleAccountImportRequest(filename="accounts.txt", content_base64=content)
    )

    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["text"] == "abc_123\nxhs-user-99"


@pytest.mark.asyncio
async def test_xhs_login_browser_reuses_cdp_and_opens_login_page(monkeypatch):
    opened = []

    monkeypatch.setattr(crawler, "_xhs_cdp_available", lambda _endpoint: True)
    monkeypatch.setattr(crawler, "_open_url_in_cdp", lambda endpoint, url: opened.append((endpoint, url)) or True)
    monkeypatch.setattr(crawler, "_focus_detected_browser", lambda: True)

    result = await crawler.xhs_login_browser()

    assert result["status"] == "login_window_opened"
    assert result["url"] == crawler.XHS_LOGIN_URL
    assert result["opened_url"] is True
    assert result["browser_focused"] is True
    assert opened == [("http://127.0.0.1:9222", crawler.XHS_LOGIN_URL)]


@pytest.mark.asyncio
async def test_sync_local_to_base_batches_new_records(monkeypatch, tmp_path):
    notes_path = tmp_path / "notes.json"
    notes = [{"note_id": f"note-{i}", "title": f"title-{i}"} for i in range(55)]
    notes_path.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    batch_payloads = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        json_arg = cmd[cmd.index("--json") + 1]
        payload_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:]
        batch_payloads.append(json.loads(payload_path.read_text(encoding="utf-8")))
        return {"ok": True, "data": {}}

    async def fake_read_table_fields(_base_token, _table_id):
        return ["note_id", "标题"]

    async def fake_read_existing_base_records(_base_token, _table_id, _dedupe_field):
        return {}

    monkeypatch.setattr(crawler, "_read_table_fields", fake_read_table_fields)
    monkeypatch.setattr(crawler, "_read_existing_base_records", fake_read_existing_base_records)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)

    result = await crawler.sync_local_to_base(
        crawler.LocalToBaseSyncRequest(
            base_token="app123",
            table_id="tbl123",
            data_type="notes",
            file_path=str(notes_path),
        )
    )

    assert result["created"] == 55
    assert result["updated"] == 0
    assert [len(payload["rows"]) for payload in batch_payloads] == [50, 5]


@pytest.mark.asyncio
async def test_sync_local_to_base_dedupes_with_chinese_note_id_field(monkeypatch, tmp_path):
    notes_path = tmp_path / "notes.json"
    notes = [
        {"note_id": "note-existing", "title": "old"},
        {"note_id": "note-new", "title": "new"},
    ]
    notes_path.write_text(json.dumps(notes, ensure_ascii=False), encoding="utf-8")
    calls = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        json_arg = cmd[cmd.index("--json") + 1]
        payload_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:]
        calls.append({"cmd": cmd, "payload": json.loads(payload_path.read_text(encoding="utf-8"))})
        return {"ok": True, "data": {}}

    async def fake_read_table_fields(_base_token, _table_id):
        return ["笔记ID", "标题"]

    async def fake_read_existing_base_records(_base_token, _table_id, dedupe_fields):
        assert "笔记ID" in dedupe_fields
        return {"note-existing": "rec123"}

    monkeypatch.setattr(crawler, "_read_table_fields", fake_read_table_fields)
    monkeypatch.setattr(crawler, "_read_existing_base_records", fake_read_existing_base_records)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)

    result = await crawler.sync_local_to_base(
        crawler.LocalToBaseSyncRequest(
            base_token="app123",
            table_id="tbl123",
            data_type="notes",
            file_path=str(notes_path),
        )
    )

    assert result["created"] == 1
    assert result["updated"] == 1
    assert [call["cmd"][2] for call in calls] == ["+record-upsert", "+record-batch-create"]
    assert calls[0]["payload"]["values"] == ["note-existing", "old"]
    assert calls[1]["payload"]["rows"] == [["note-new", "new"]]
