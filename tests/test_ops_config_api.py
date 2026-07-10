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
        collab_comments_table_name="合作笔记舆情监控表",
        collab_comments_table_id="tbl_collab_comments",
    )
    data = payload.model_dump()

    assert OPS_CONFIG_DEFAULT["collab_comments_table_name"] == "合作笔记舆情监控表"
    assert OPS_CONFIG_DEFAULT["collab_comments_table_id"] == ""
    assert data["collab_comments_table_name"] == "合作笔记舆情监控表"
    assert data["collab_comments_table_id"] == "tbl_collab_comments"
    assert "collab_comments_table_id" in PROJECT_BOUND_FIELDS


def test_ops_config_defaults_to_customer_template_table_names():
    assert OPS_CONFIG_DEFAULT["account_filter_table_name"] == "账号内容监测表"
    assert OPS_CONFIG_DEFAULT["viral_monitor_table_name"] == "平台爆款监测表"
    assert OPS_CONFIG_DEFAULT["note_recreation_table_name"] == "笔记内容二创表"
    assert OPS_CONFIG_DEFAULT["comments_table_name"] == "笔记舆情监控表"
    assert OPS_CONFIG_DEFAULT["collaboration_monitor_table_name"] == "笔记数据监测表"
    assert OPS_CONFIG_DEFAULT["collab_comments_table_name"] == "合作笔记舆情监控表"


def test_scenario_setup_fields_match_customer_template():
    viral_fields = [field["name"] for field in crawler._viral_monitor_fields()]
    creator_fields = [field["name"] for field in crawler._creator_selection_fields()]
    account_fields = [field["name"] for field in crawler._account_content_monitor_fields()]
    note_fields = [field["name"] for field in crawler._note_data_monitor_fields()]
    sentiment_fields = [field["name"] for field in crawler._sentiment_monitor_fields()]
    recreation_fields = [field["name"] for field in crawler._note_recreation_fields()]

    assert viral_fields == [
        "归属项目", "检索关键词", "笔记发布时间", "博主名", "博主ID", "博主粉丝数", "博主主页", "笔记类型",
        "笔记标题", "笔记内容", "笔记封面", "笔记图片1", "笔记tag", "点赞", "收藏数", "分享数", "评论数",
        "阅读量", "曝光量", "总互动数据（赞+藏+评，不算分享）", "采集数据时间", "封面文件",
    ]
    assert creator_fields[:9] == [
        "目标/推荐博主", "推荐排名", "目标达人昵称", "达人昵称", "小红书号", "主页链接", "蒲公英主页链接",
        "内容类目（标签）", "合作行业",
    ]
    assert "合作笔记图文3秒阅读率" in creator_fields
    assert account_fields[:10] == [
        "达人昵称", "小红书号", "主页链接", "蒲公英主页链接", "发布笔记倒序（发布时间由近及远）",
        "笔记链接", "笔记标题", "笔记内容", "笔记封面", "笔记tag",
    ]
    assert note_fields == [
        "序号", "达人昵称", "小红书id", "发布笔记链接", "发布时间", "笔记tag", "笔记标题", "点赞", "收藏",
        "评论", "总互动（点赞+收藏+评论）", "分享", "曝光量", "阅读量", "笔记失效/正常（有失效链接作标记）",
    ]
    assert sentiment_fields[:7] == [
        "项目名", "笔记链接", "笔记标题", "评论总数", "评论区敏感词", "评论区敏感词监测（是/否）", "评论区分析",
    ]
    assert recreation_fields == [
        "收藏数", "当日使用标记", "改写打分", "笔记ID", "博主名", "笔记链接", "标题", "采集时间",
        "博主主页", "标题改写", "关键词", "点赞数", "评论数", "内容", "笔记类型", "首发时间",
        "分享数", "博主粉丝数", "封面图", "已使用账号记录", "项目名", "正文改写", "话题标签",
    ]


def test_historical_note_row_maps_to_customer_viral_payload():
    table_fields = [field["name"] for field in crawler._viral_monitor_fields()]
    row = {
        "source_keyword": "护肤",
        "time": 1710000000000,
        "author_nickname": "历史达人",
        "author_user_id": "xhs_user_1",
        "author_fans_count": "1.2万",
        "note_type": "normal",
        "title": "历史爆款标题",
        "desc": "历史正文",
        "image_list": ["https://img.example/1.jpg", "https://img.example/2.jpg"],
        "tag_list": ["护肤", "测评"],
        "liked_count": "100",
        "collected_count": "20",
        "share_count": "5",
        "comment_count": "7",
        "last_update_time": 1710003600000,
    }

    values = crawler._row_to_table_values(row, table_fields, "notes")
    payload = dict(zip(table_fields, values))

    assert payload["检索关键词"] == "护肤"
    assert payload["博主ID"] == "xhs_user_1"
    assert payload["博主粉丝数"] == 12000
    assert payload["笔记类型"] == "图文"
    assert payload["笔记封面"] == "https://img.example/1.jpg"
    assert payload["笔记图片1"] == "https://img.example/1.jpg"
    assert payload["笔记tag"] == "护肤,测评"
    assert payload["总互动数据（赞+藏+评，不算分享）"] == 127


def test_historical_note_row_maps_to_customer_recreation_payload():
    table_fields = [field["name"] for field in crawler._note_recreation_fields()]
    row = {
        "project_name": "二创项目",
        "source_keyword": "徒步",
        "note_id": "note_1",
        "time": 1710000000000,
        "author_nickname": "历史达人",
        "author_user_id": "xhs_user_1",
        "author_fans_count": "1.2万",
        "note_type": "video",
        "title": "原始标题",
        "desc": "原始正文",
        "image_list": "https://img.example/1.jpg,https://img.example/2.jpg",
        "tag_list": ["户外", "路线"],
        "liked_count": "100",
        "collected_count": "20",
        "share_count": "5",
        "comment_count": "7",
        "note_url": "https://www.xiaohongshu.com/explore/note_1",
        "last_update_time": 1710003600000,
    }

    values = crawler._row_to_table_values(row, table_fields, "notes")
    payload = dict(zip(table_fields, values))

    assert payload["项目名"] == "二创项目"
    assert payload["关键词"] == "徒步"
    assert payload["笔记ID"] == "note_1"
    assert payload["标题"] == "原始标题"
    assert payload["内容"] == "原始正文"
    assert payload["封面图"] == "https://img.example/1.jpg"
    assert payload["话题标签"] == "户外,路线"
    assert payload["博主粉丝数"] == 12000
    assert payload["笔记类型"] == "视频"


@pytest.mark.asyncio
async def test_read_table_fields_skips_not_support_columns(monkeypatch):
    payload = {
        "data": {
            "fields": [
                {"name": "标题", "type": "text"},
                {"name": "标题改写", "type": "not_support"},
                {"name": "正文改写", "type": "not_support"},
                {"name": "封面文件", "type": "attachment"},
                {"name": "点赞数", "type": "number"},
            ]
        }
    }

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        return payload

    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")

    names = await crawler._read_table_fields("base_token", "table_id")

    assert names == ["标题", "点赞数"]


def test_cover_url_extracts_first_media_url():
    assert crawler._cover_url_from_row({"image_list": "https://img.example/a.jpg,https://img.example/b.jpg"}) == "https://img.example/a.jpg"
    assert crawler._cover_url_from_row({"image_list": [{"url_default": "https://img.example/c.jpg"}]}) == "https://img.example/c.jpg"
    assert crawler._cover_url_from_row({"image_list": '[{"url":"https://img.example/d.webp"}]'}) == "https://img.example/d.webp"


def test_local_cover_file_prefers_runtime_media_dir(monkeypatch, tmp_path):
    media_file = tmp_path / "xhs" / "images" / "note-1" / "0.jpg"
    media_file.parent.mkdir(parents=True)
    media_file.write_bytes(b"fake image")

    monkeypatch.setattr(crawler, "data_dir", lambda: tmp_path)

    assert crawler._local_cover_file_from_row({"note_id": "note-1"}) == media_file


@pytest.mark.asyncio
async def test_upload_base_attachment_stages_external_file(monkeypatch, tmp_path):
    external_file = tmp_path / "runtime-data" / "cover.jpg"
    external_file.parent.mkdir(parents=True)
    external_file.write_bytes(b"fake image")
    captured_files = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        cli_file = cmd[cmd.index("--file") + 1]
        project_root = Path(crawler.__file__).resolve().parents[2]
        staged_file = project_root / cli_file
        captured_files.append(cli_file)
        assert not Path(cli_file).is_absolute()
        assert staged_file.exists()
        assert staged_file.read_bytes() == b"fake image"
        return {"ok": True}

    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")

    await crawler._upload_base_attachment("app123", "tbl123", "rec123", "封面文件", str(external_file))

    assert len(captured_files) == 1
    assert captured_files[0].startswith(".tmp_lark_uploads/")
    assert not (Path(crawler.__file__).resolve().parents[2] / captured_files[0]).exists()


@pytest.mark.asyncio
async def test_upload_cover_file_uses_attachment_field_id(monkeypatch, tmp_path):
    cover_file = tmp_path / "cover.jpg"
    cover_file.write_bytes(b"fake image")
    captured_field_ids = []

    async def fake_upload(base_token, table_id, record_id, field_id, file_path):
        captured_field_ids.append(field_id)

    monkeypatch.setattr(crawler, "_local_cover_file_from_row", lambda row: cover_file)
    monkeypatch.setattr(crawler, "_upload_base_attachment", fake_upload)

    uploaded, error = await crawler._upload_cover_file_if_available(
        "app123",
        "tbl123",
        "rec123",
        {"note_id": "note-1"},
        {"封面文件": "fld5NvqX7K"},
    )

    assert uploaded is True
    assert error == ""
    assert captured_field_ids == ["fld5NvqX7K"]


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

    async def fake_read_table_field_defs(_base_token, _table_id):
        return [{"name": "note_id", "type": "text"}, {"name": "标题", "type": "text"}, {"name": "封面文件", "type": "attachment"}]

    async def fake_read_existing_base_records(_base_token, _table_id, _dedupe_field):
        return {}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read_table_field_defs)
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

    async def fake_read_table_field_defs(_base_token, _table_id):
        return [{"name": "笔记ID", "type": "text"}, {"name": "标题", "type": "text"}, {"name": "封面文件", "type": "attachment"}]

    async def fake_read_existing_base_records(_base_token, _table_id, dedupe_fields):
        assert "笔记ID" in dedupe_fields
        return {"note-existing": "rec123"}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read_table_field_defs)
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
