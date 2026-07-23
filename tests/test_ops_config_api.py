# -*- coding: utf-8 -*-
import base64
import json
import ssl
import urllib.error
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
    assert OPS_CONFIG_DEFAULT["sentiment_risk_keywords"] == "骗人,差,价格高,贵,jd,pdd"
    assert OPS_CONFIG_DEFAULT["sentiment_risk_groups"] == '[{"name":"电商风险","keywords":"骗人,差,价格高,贵,jd,pdd"}]'
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
        "阅读量", "曝光量", "总互动数据（赞+藏+评，不算分享）", "采集数据时间", "笔记封面URL",
    ]
    viral_cover = next(field for field in crawler._viral_monitor_fields() if field["name"] == "笔记封面")
    assert viral_cover["type"] == "attachment"
    assert [field["name"] for field in crawler._comments_fields()] == [
        "IP属地", "评论内容", "评论用户", "项目名", "二级评论数", "点赞数", "评论时间", "评论图片",
        "笔记ID", "关键词", "父评论ID", "评论区分析",
    ]
    assert creator_fields[:9] == [
        "目标/推荐博主", "推荐排名", "目标达人昵称", "达人昵称", "小红书号", "主页链接", "蒲公英主页链接",
        "内容类目（标签）", "合作行业",
    ]
    assert "合作笔记图文3秒阅读率" in creator_fields
    assert account_fields[:12] == [
        "达人昵称", "小红书号", "主页链接", "蒲公英主页链接", "蒲公英主页状态", "蒲公英查询依据",
        "发布笔记倒序（发布时间由近及远）", "笔记链接", "笔记标题", "笔记内容", "笔记封面", "笔记tag",
    ]
    assert note_fields == [
        "序号", "达人昵称", "小红书id", "发布笔记链接", "发布时间", "笔记tag", "笔记标题", "点赞", "收藏",
        "评论", "总互动（点赞+收藏+评论）", "分享", "曝光量", "阅读量", "笔记失效/正常（有失效链接作标记）",
    ]
    assert sentiment_fields[:8] == [
        "项目名", "笔记链接", "笔记标题", "点赞数", "评论总数",
        "评论区敏感词", "评论区敏感词监测", "舆情风险",
    ]
    risk_field = next(field for field in crawler._sentiment_monitor_fields() if field["name"] == "舆情风险")
    assert risk_field == {
        "name": "舆情风险",
        "type": "formula",
        "expression": "\"\"",
        "description": "笔记舆情监控自动汇总的风险类型",
    }
    keyword_field = next(
        field for field in crawler._sentiment_monitor_fields() if field["name"] == "评论区敏感词"
    )
    monitor_field = next(
        field for field in crawler._sentiment_monitor_fields() if field["name"] == "评论区敏感词监测"
    )
    assert keyword_field["type"] == "formula"
    assert monitor_field["type"] == "formula"
    assert recreation_fields == [
        "收藏数", "当日使用标记", "改写打分", "笔记ID", "博主名", "笔记链接", "标题", "采集时间",
        "博主主页", "标题改写.输出结果", "封面改写", "关键词", "点赞数", "评论数", "内容", "笔记类型", "首发时间",
        "分享数", "博主粉丝数", "封面附件", "已使用账号记录", "项目名", "正文改写.输出结果", "二次调整口令",
        "二次标题改写", "二次正文改写", "二次图片改写", "话题标签",
    ]
    assert crawler._account_content_monitor_public_field_names() == [
        "达人昵称", "小红书号", "主页链接", "蒲公英主页链接",
        "发布笔记倒序（发布时间由近及远）", "笔记链接", "笔记标题", "笔记封面",
        "笔记tag", "点赞", "收藏", "评论", "笔记总互动量（点赞+收藏+评论）",
    ]


def test_account_monitor_report_rows_keep_public_and_pgy_fields_separate():
    source = {
        "author_nickname": "测试达人",
        "author_user_id": "profile_123",
        "title": "最新笔记",
        "note_url": "https://www.xiaohongshu.com/explore/note_1",
        "liked_count": "10",
        "collected_count": "20",
        "comment_count": "3",
    }
    public_row = crawler._account_monitor_public_row(source)
    assert public_row["达人昵称"] == "测试达人"
    assert public_row["点赞"] == 10
    assert public_row["笔记总互动量（点赞+收藏+评论）"] == 33

    pgy_row = crawler._account_monitor_pgy_row(source, {
        "nickname": "蒲公英达人",
        "red_id": "red_123",
        "url": "https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/pgy_123",
        "target_metrics": {"fans_num": 1234, "daily_imp_median": 88},
    })
    assert pgy_row["达人昵称"] == "测试达人"
    assert pgy_row["小红书号"] == "red_123"
    assert pgy_row["粉丝数"] == 1234
    assert pgy_row["日常笔记曝光中位数"] == 88
    assert pgy_row["点赞"] == 10

    no_pgy_row = crawler._account_monitor_pgy_row(source, None, {
        "status": "无蒲公英主页", "evidence": "蒲公英“找博主”按昵称查询：暂无结果",
    })
    assert no_pgy_row["蒲公英主页状态"] == "无蒲公英主页"
    assert "暂无结果" in no_pgy_row["蒲公英查询依据"]


def test_pgy_login_required_normalizes_automation_errors():
    assert crawler._pgy_login_required({
        "status": "error",
        "error": "蒲公英需要登录，请先运行 login 动作",
        "returncode": 1,
    }) is True
    assert crawler._pgy_login_required({"status": "not_found"}) is False


@pytest.mark.asyncio
async def test_start_account_monitor_snapshots_current_creator_and_uses_isolated_csv(monkeypatch):
    captured = {}

    async def fake_start(start_request):
        captured["start_request"] = start_request
        return True

    async def fake_build(*args, **kwargs):
        return None

    monkeypatch.setattr(crawler.crawler_manager, "process", None)
    monkeypatch.setattr(crawler.crawler_manager, "start", fake_start)
    monkeypatch.setattr(crawler, "_clear_creator_data_files", lambda: None)
    monkeypatch.setattr(crawler, "_build_account_monitor_report", fake_build)

    result = await crawler.start_account_monitor(crawler.SampleCreatorStartRequest(
        creator_ids="https://www.xiaohongshu.com/user/profile/62c98736000000001501e075",
        notes_per_creator=37,
        save_option="excel",
        report_mode="auto",
    ))
    job_id = result["job_id"]

    try:
        await crawler.account_monitor_jobs[job_id]["task"]
        job = crawler.account_monitor_jobs[job_id]

        assert result["creator_count"] == 1
        assert job["requested_creator_ids"] == ["62c98736000000001501e075"]
        assert job["source_started_at"] > 0
        assert captured["start_request"].creator_ids.endswith("/62c98736000000001501e075")
        assert captured["start_request"].max_notes_count == 10
        assert captured["start_request"].save_option.value == "csv"
        assert job["notes_per_creator"] == 10
        assert result["notes_per_creator"] == 10
    finally:
        crawler.account_monitor_jobs.pop(job_id, None)


@pytest.mark.asyncio
async def test_account_monitor_stops_remaining_pgy_queries_after_login_required(monkeypatch, tmp_path):
    job_id = "pgy_login_required_job"
    calls = []

    async def fake_wait_crawler_idle(timeout_sec):
        return True

    async def fake_run_pgy(args, timeout_sec):
        calls.append(args[2])
        return {
            "status": "login_required",
            "error": "蒲公英需要登录，请先运行 login 动作",
            "returncode": 0,
        }

    source_rows = [
        {"author_user_id": "historical_creator", "author_nickname": "历史达人"},
        {"author_user_id": "creator_a", "author_nickname": "达人A"},
        {"author_user_id": "creator_b", "author_nickname": "达人B"},
        {"author_user_id": "creator_c", "author_nickname": "达人C"},
    ]
    monkeypatch.setattr(crawler, "_wait_crawler_idle", fake_wait_crawler_idle)
    monkeypatch.setattr(crawler, "_latest_local_file", lambda *_, **__: tmp_path / "source.jsonl")
    monkeypatch.setattr(crawler, "_read_local_rows", lambda *_: source_rows)
    monkeypatch.setattr(crawler, "_run_pgy_automation", fake_run_pgy)
    monkeypatch.setattr(crawler, "_write_account_monitor_report", lambda *_: tmp_path / "report.xlsx")
    crawler.account_monitor_jobs[job_id] = {
        "job_id": job_id,
        "requested_creator_ids": ["creator_a", "creator_b", "creator_c"],
        "source_started_at": 123.0,
    }

    try:
        await crawler._build_account_monitor_report(job_id, "auto")
        job = crawler.account_monitor_jobs[job_id]

        assert calls == ["达人A"]
        assert job["status"] == "completed"
        assert job["pgy_login_required"] is True
        assert job["pgy_login_accounts"] == ["达人A", "达人B", "达人C"]
        assert job["pgy_review_count"] == 3
        assert len(job["pgy_errors"]) == 3
        assert job["ignored_historical_row_count"] == 1
        assert job["_pgy_pending_keys"] == ["creator_a", "creator_b", "creator_c"]
    finally:
        crawler.account_monitor_jobs.pop(job_id, None)


@pytest.mark.asyncio
async def test_account_monitor_resumes_pgy_checkpoint_without_recrawling_xhs(monkeypatch, tmp_path):
    job_id = "resume_pgy_checkpoint_job"
    source_path = tmp_path / "creator_contents.jsonl"
    source_path.write_text("{}\n", encoding="utf-8")
    pgy_calls = []

    async def fail_if_waiting_for_xhs(timeout_sec):
        raise AssertionError("resume must not wait for or rerun the Xiaohongshu crawler")

    async def fake_run_pgy(args, timeout_sec):
        pgy_calls.append(args[2])
        return {"status": "not_found", "returncode": 0}

    source_rows = [
        {"author_user_id": "creator_a", "author_nickname": "达人A"},
        {"author_user_id": "creator_b", "author_nickname": "达人B"},
        {"author_user_id": "creator_c", "author_nickname": "达人C"},
    ]
    monkeypatch.setattr(crawler, "_wait_crawler_idle", fail_if_waiting_for_xhs)
    monkeypatch.setattr(crawler, "_read_local_rows", lambda *_: source_rows)
    monkeypatch.setattr(crawler, "_run_pgy_automation", fake_run_pgy)
    monkeypatch.setattr(crawler, "_write_account_monitor_report", lambda *_: tmp_path / "report.xlsx")
    crawler.account_monitor_jobs[job_id] = {
        "job_id": job_id,
        "status": "completed",
        "report_mode": "auto",
        "source_path": str(source_path),
        "requested_creator_ids": ["creator_a", "creator_b", "creator_c"],
        "pgy_login_required": True,
        "pgy_login_accounts": ["达人B", "达人C"],
        "_pgy_pending_keys": ["creator_b", "creator_c"],
        "_pgy_summaries": {"creator_a": {"nickname": "达人A"}},
        "_pgy_lookups": {"creator_a": {"status": "有蒲公英主页", "evidence": "已完成"}},
        "base_token": "",
        "table_id": "",
    }

    try:
        result = await crawler.resume_account_monitor_pgy(job_id)
        await crawler.account_monitor_jobs[job_id]["task"]
        job = crawler.account_monitor_jobs[job_id]

        assert result["remaining_creator_count"] == 2
        assert "不会重复抓取小红书" in result["message"]
        assert pgy_calls == ["达人B", "达人C"]
        assert job["status"] == "completed"
        assert job["pgy_login_required"] is False
        assert job["pgy_found_count"] == 1
        assert job["pgy_not_found_count"] == 2
        assert job["_pgy_pending_keys"] == []
    finally:
        crawler.account_monitor_jobs.pop(job_id, None)


@pytest.mark.asyncio
async def test_account_monitor_rejects_historical_rows_when_current_creator_has_no_data(monkeypatch, tmp_path):
    job_id = "account_monitor_current_creator_missing"

    async def fake_wait_crawler_idle(timeout_sec):
        return True

    monkeypatch.setattr(crawler, "_wait_crawler_idle", fake_wait_crawler_idle)
    monkeypatch.setattr(crawler, "_latest_local_file", lambda *_, **__: tmp_path / "source.jsonl")
    monkeypatch.setattr(crawler, "_read_local_rows", lambda *_: [
        {"author_user_id": "historical_creator", "author_nickname": "历史达人"},
    ])
    crawler.account_monitor_jobs[job_id] = {
        "job_id": job_id,
        "requested_creator_ids": ["current_creator"],
        "source_started_at": 123.0,
    }

    try:
        await crawler._build_account_monitor_report(job_id, "auto")
        job = crawler.account_monitor_jobs[job_id]

        assert job["status"] == "error"
        assert "已阻止使用历史账号数据" in job["error"]
        assert job["ignored_historical_row_count"] == 1
    finally:
        crawler.account_monitor_jobs.pop(job_id, None)


def test_account_monitor_report_writes_selected_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(crawler, "temp_dir", lambda: tmp_path)

    report = crawler._write_account_monitor_report(
        [{"达人昵称": "测试达人", "点赞": 10}], "public", "job_12345678"
    )

    assert report.exists()
    assert report.suffix == ".xlsx"
    import pandas as pd
    assert list(pd.read_excel(report).columns) == crawler._account_content_monitor_public_field_names()


def test_creator_selection_type_field_uses_colored_single_select_options():
    field = next(item for item in crawler._creator_selection_fields() if item["name"] == "目标/推荐博主")

    assert field["type"] == "select"
    assert field["multiple"] is False
    assert {item["name"] for item in field["options"]} == {"目标达人", "相似博主"}
    assert {item["hue"] for item in field["options"]} == {"Blue", "Orange"}


def test_account_monitor_status_field_uses_colored_single_select_options():
    field = next(
        item for item in crawler._account_content_monitor_fields()
        if item["name"] == "蒲公英主页状态"
    )

    assert field["type"] == "select"
    assert field["multiple"] is False
    assert {item["name"] for item in field["options"]} == {
        "有蒲公英主页", "无蒲公英主页", "待人工确认",
    }
    assert {item["hue"] for item in field["options"]} == {"Green", "Gray", "Yellow"}


@pytest.mark.asyncio
async def test_account_monitor_template_repair_adds_status_fields_and_filtered_views(monkeypatch):
    wanted = crawler._account_content_monitor_fields()
    first_read = [
        field for field in wanted
        if field["name"] not in {"蒲公英主页状态", "蒲公英查询依据"}
    ]
    refreshed = [
        ({**field, "id": "fld_status"} if field["name"] == "蒲公英主页状态" else {**field, "id": f"fld_{index}"})
        for index, field in enumerate(wanted)
    ]
    reads = [first_read, refreshed]
    created_fields = []
    cli_calls = []
    visible_calls = []
    view_reads = [
        [{"id": "vew_main", "name": "Grid View", "type": "grid"}],
        [
            {"id": "vew_main", "name": "Grid View", "type": "grid"},
            {"id": "vew_yes", "name": "有蒲公英主页", "type": "grid"},
            {"id": "vew_no", "name": "无蒲公英主页", "type": "grid"},
            {"id": "vew_review", "name": "待人工确认", "type": "grid"},
        ],
    ]

    async def fake_read_fields(*_):
        return reads.pop(0)

    async def fake_create_field(_base_token, _table_id, field):
        created_fields.append(field)

    async def fake_set_order(*_):
        return None

    async def fake_list_views(*_):
        return view_reads.pop(0)

    async def fake_run_cli(command, timeout_sec=30):
        cli_calls.append(command)
        return {"ok": True, "data": {}}

    async def fake_set_visible(_base_token, _table_id, view_id, fields):
        visible_calls.append((view_id, fields))

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read_fields)
    monkeypatch.setattr(crawler, "_create_base_field", fake_create_field)
    monkeypatch.setattr(crawler, "_set_table_view_field_order", fake_set_order)
    monkeypatch.setattr(crawler, "_list_table_views", fake_list_views)
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_cli)
    monkeypatch.setattr(crawler, "_set_view_visible_fields", fake_set_visible)

    await crawler._ensure_account_content_monitor_fields("base_template", "tbl_account")

    assert {field["name"] for field in created_fields} == {"蒲公英主页状态", "蒲公英查询依据"}
    assert sum("+view-create" in call for call in cli_calls) == 3
    filter_calls = [call for call in cli_calls if "+view-set-filter" in call]
    assert len(filter_calls) == 3
    assert all("fld_status" in call[call.index("--json") + 1] for call in filter_calls)
    no_pgy_fields = dict(visible_calls)["vew_no"]
    assert "蒲公英主页状态" in no_pgy_fields
    assert "日常笔记曝光中位数" not in no_pgy_fields


@pytest.mark.asyncio
async def test_viral_template_migration_preserves_cover_url_and_renames_attachment(monkeypatch):
    wanted = crawler._viral_monitor_fields()
    initial = [
        *[
            field for field in wanted
            if field["name"] not in {"笔记封面", "笔记封面URL"}
        ],
        {"id": "fld_cover_text", "name": "笔记封面", "type": "text", "style": {"type": "plain"}},
        {"id": "fld_cover_attachment", "name": "封面文件", "type": "attachment"},
    ]
    after_url_rename = [
        *[field for field in initial if field.get("id") not in {"fld_cover_text"}],
        {"id": "fld_cover_text", "name": "笔记封面URL", "type": "text", "style": {"type": "plain"}},
    ]
    migrated = [
        *[field for field in after_url_rename if field.get("id") != "fld_cover_attachment"],
        {"id": "fld_cover_attachment", "name": "笔记封面", "type": "attachment"},
    ]
    reads = [initial, after_url_rename, migrated, migrated]
    updates = []
    visible_orders = []

    async def fake_read(*_):
        return reads.pop(0)

    async def fake_update(_base_token, _table_id, field_id, field):
        updates.append((field_id, field))

    async def fake_create(*_):
        raise AssertionError("migration should reuse the existing attachment field")

    async def fake_order(_base_token, _table_id, fields):
        visible_orders.append(fields)

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read)
    monkeypatch.setattr(crawler, "_update_base_field", fake_update)
    monkeypatch.setattr(crawler, "_create_base_field", fake_create)
    monkeypatch.setattr(crawler, "_set_table_view_field_order", fake_order)

    result = await crawler._ensure_viral_monitor_fields("base_template", "tbl_viral")

    assert updates == [
        ("fld_cover_text", crawler._text_field("笔记封面URL")),
        ("fld_cover_attachment", crawler._attachment_field("笔记封面")),
    ]
    assert next(field for field in result if field["name"] == "笔记封面")["type"] == "attachment"
    assert "笔记图片1" not in visible_orders[0]
    assert "笔记封面URL" not in visible_orders[0]
    assert "笔记封面" in visible_orders[0]


@pytest.mark.asyncio
async def test_scenario_setup_repairs_reused_account_monitor_table(monkeypatch):
    request = crawler.ScenarioTableSetupRequest(base_token="base_template")
    table_names = [
        request.account_filter_table_name,
        request.viral_monitor_table_name,
        request.note_recreation_table_name,
        request.comments_table_name,
        request.collaboration_monitor_table_name,
        request.collab_comments_table_name,
        request.creator_selection_table_name,
        request.creator_screening_table_name,
    ]
    repaired = []

    async def fake_list_tables(_base_token):
        return [{"name": name, "id": f"tbl_{index}"} for index, name in enumerate(table_names)]

    async def fake_account_repair(base_token, table_id):
        repaired.append((base_token, table_id))
        return []

    async def no_op(*_, **__):
        return []

    monkeypatch.setattr(crawler, "_list_base_tables", fake_list_tables)
    monkeypatch.setattr(crawler, "_ensure_account_content_monitor_fields", fake_account_repair)
    monkeypatch.setattr(crawler, "_ensure_note_recreation_fields", no_op)
    monkeypatch.setattr(crawler, "_ensure_viral_monitor_fields", no_op)
    monkeypatch.setattr(crawler, "_ensure_fields_and_view_order", no_op)
    monkeypatch.setattr(crawler, "_ensure_creator_selection_fields", no_op)

    result = await crawler.setup_scenario_tables(request)

    assert result["status"] == "ok"
    assert repaired == [("base_template", "tbl_0")]
    assert all(item["reused"] is True for item in result["tables"])
    assert [item["table_name"] for item in result["tables"]] == [
        request.viral_monitor_table_name,
        request.comments_table_name,
        request.creator_selection_table_name,
        request.account_filter_table_name,
        request.note_recreation_table_name,
        request.collaboration_monitor_table_name,
        request.collab_comments_table_name,
        request.creator_screening_table_name,
    ]


def test_creator_screening_result_table_matches_original_input_columns():
    assert [field["name"] for field in crawler._creator_screening_result_fields()] == [
        "达人昵称", "博主ID", "主页链接", "达人价格",
    ]


def test_collaboration_note_form_parser_requires_customer_columns():
    rows = crawler._parse_collaboration_note_file(
        "合作笔记.csv",
        "序号,达人昵称,小红书id,发布笔记链接\n1,甲,red_1,https://www.xiaohongshu.com/explore/note_1\n".encode("utf-8"),
    )

    assert rows == [{
        "序号": "1",
        "达人昵称": "甲",
        "小红书id": "red_1",
        "发布笔记链接": "https://www.xiaohongshu.com/explore/note_1",
    }]

    with pytest.raises(crawler.HTTPException, match="缺少必需列"):
        crawler._parse_collaboration_note_file(
            "合作笔记.csv",
            "达人昵称,发布笔记链接\n甲,https://www.xiaohongshu.com/explore/note_1\n".encode("utf-8"),
        )


@pytest.mark.asyncio
async def test_sentiment_note_form_import_returns_clean_note_links():
    content = base64.b64encode(
        (
            "序号,达人昵称,小红书id,发布笔记链接\n"
            "1,甲,red_1,分享文案 https://www.xiaohongshu.com/explore/note_1?xsec_token=token\n"
        ).encode("utf-8")
    ).decode("ascii")

    result = await crawler.import_sentiment_notes(
        crawler.SampleAccountImportRequest(filename="舆情笔记.csv", content_base64=content)
    )

    assert result["count"] == 1
    assert result["note_links"] == "https://www.xiaohongshu.com/explore/note_1?xsec_token=token"


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
                {"name": "序号", "type": "auto_number"},
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
    captured_files = []

    async def fake_upload(base_token, table_id, record_id, field_id, file_paths):
        captured_field_ids.append(field_id)
        captured_files.extend(file_paths)

    monkeypatch.setattr(crawler, "_local_cover_files_from_row", lambda row: [cover_file])
    monkeypatch.setattr(crawler, "_upload_base_attachments", fake_upload)

    uploaded, error = await crawler._upload_cover_file_if_available(
        "app123",
        "tbl123",
        "rec123",
        {"note_id": "note-1"},
        {"笔记封面": "fld5NvqX7K"},
    )

    assert uploaded is True
    assert error == ""
    assert captured_field_ids == ["fld5NvqX7K"]
    assert captured_files == [cover_file]


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
    content = "主页链接\nhttps://www.xiaohongshu.com/user/profile/abc_123\nhttps://xhslink.com/a1b2\nhttps://www.xiaohongshu.com/user/profile/abc_123\n".encode("utf-8")

    accounts = crawler._parse_sample_account_file("accounts.txt", content)

    assert accounts == ["https://www.xiaohongshu.com/user/profile/abc_123", "https://xhslink.com/a1b2"]


def test_parse_sample_accounts_extracts_urls_from_share_text_and_cells():
    content = (
        "主页链接\n"
        "达人A：复制后打开小红书 https://www.xiaohongshu.com/user/profile/abc_123?xsec_token=token 备注\n"
        "达人B https://xhslink.com/m/4sMLPmRKj8e，短链接\n"
    ).encode("utf-8")

    accounts = crawler._parse_sample_account_file("accounts.txt", content)

    assert accounts == [
        "https://www.xiaohongshu.com/user/profile/abc_123?xsec_token=token",
        "https://xhslink.com/m/4sMLPmRKj8e",
    ]


@pytest.mark.asyncio
async def test_import_sample_accounts_endpoint_accepts_base64_txt():
    content = base64.b64encode("主页链接\nhttps://www.xiaohongshu.com/user/profile/abc_123\nhttps://xhslink.com/a1b2\n".encode("utf-8")).decode("ascii")

    result = await crawler.import_sample_accounts(
        crawler.SampleAccountImportRequest(filename="accounts.txt", content_base64=content)
    )

    assert result["status"] == "ok"
    assert result["count"] == 2
    assert result["text"] == "https://www.xiaohongshu.com/user/profile/abc_123\nhttps://xhslink.com/a1b2"


def test_account_monitor_normalizes_a_long_profile_link():
    assert crawler._profile_url_from_link(
        "https://www.xiaohongshu.com/user/profile/abc_123?xsec_token=token"
    ) == "https://www.xiaohongshu.com/user/profile/abc_123"


def test_account_monitor_cleans_share_text_to_a_profile_url():
    assert crawler._profile_url_from_link(
        "达人主页： https://www.xiaohongshu.com/user/profile/abc_123?xsec_token=token，来源微信"
    ) == "https://www.xiaohongshu.com/user/profile/abc_123"


def test_account_monitor_resolves_a_short_profile_link(monkeypatch):
    class FakeResponse:
        def geturl(self):
            return "https://www.xiaohongshu.com/user/profile/short_123?xsec_token=token"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def open(self, _request, timeout):
            assert timeout == 15
            return FakeResponse()

    monkeypatch.setattr(crawler.urllib.request, "build_opener", lambda *_handlers: FakeOpener())

    assert crawler._profile_url_from_link("https://xhslink.com/short-link") == "https://www.xiaohongshu.com/user/profile/short_123"


def test_xhs_short_link_retries_only_certificate_failure_without_verification(monkeypatch):
    calls = []

    class FakeResponse:
        def geturl(self):
            return "https://www.xiaohongshu.com/user/profile/short_456"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class FakeOpener:
        def __init__(self, verify):
            self.verify = verify

        def open(self, _request, timeout):
            calls.append((self.verify, timeout))
            if self.verify:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
            return FakeResponse()

    monkeypatch.setattr(crawler, "_build_xhs_url_opener", lambda verify_certificates: FakeOpener(verify_certificates))

    assert crawler._resolve_xhs_redirect_url("https://xhslink.com/m/test") == (
        "https://www.xiaohongshu.com/user/profile/short_456"
    )
    assert calls == [(True, 15), (False, 15)]


def test_sentiment_keyword_formula_lists_actual_matches():
    groups = crawler._normalize_sentiment_risk_groups([
        {"name": "电商风险", "keywords": "投诉,价格高"},
    ])

    assert crawler._sentiment_keyword_formula(groups) == (
        'REGEXREPLACE(CONCATENATE(IF(CONTAINTEXT([评论内容], "投诉"), "投诉、", ""), '
        'IF(CONTAINTEXT([评论内容], "价格高"), "价格高、", "")), "、$", "")'
    )
    assert crawler._sentiment_monitor_formula() == 'IF([评论区敏感词] != "", "命中", "未命中")'


def test_sentiment_formula_marks_any_keyword_match():
    keywords = crawler._split_sentiment_keywords("投诉，质量问题\n欺骗,投诉")

    assert keywords == ["投诉", "质量问题", "欺骗"]
    assert crawler._sentiment_risk_formula(keywords) == (
        'IF(OR(CONTAINTEXT([评论内容], "投诉"), CONTAINTEXT([评论内容], "质量问题"), '
        'CONTAINTEXT([评论内容], "欺骗")), "电商风险", "")'
    )


def test_sentiment_risk_groups_create_per_type_and_summary_formulas():
    groups = crawler._normalize_sentiment_risk_groups([
        {"name": "电商风险", "keywords": "骗人,价格高"},
        {"name": "服务风险", "keywords": "不回复\n拖延"},
    ])

    assert groups == [
        {"name": "电商风险", "keywords": ["骗人", "价格高"]},
        {"name": "服务风险", "keywords": ["不回复", "拖延"]},
    ]
    assert crawler._normalize_sentiment_risk_groups(groups) == groups
    assert crawler._risk_group_field_name("电商风险") == "风险-电商风险"
    assert crawler._risk_group_formula(groups[0]) == (
        'IF(OR(CONTAINTEXT([评论内容], "骗人"), CONTAINTEXT([评论内容], "价格高")), "电商风险", "")'
    )
    assert crawler._sentiment_risk_formula(groups) == (
        'REGEXREPLACE(CONCATENATE(IF(OR(CONTAINTEXT([评论内容], "骗人"), '
        'CONTAINTEXT([评论内容], "价格高")), "电商风险、", ""), '
        'IF(OR(CONTAINTEXT([评论内容], "不回复"), CONTAINTEXT([评论内容], "拖延")), "服务风险、", "")), "、$", "")'
    )


def test_sentiment_note_link_extracts_note_id():
    assert crawler._note_id_from_link("https://www.xiaohongshu.com/explore/note_123?xsec_token=token") == "note_123"
    assert crawler._normalized_note_url_from_link(
        "https://www.xiaohongshu.com/explore/note_123?xsec_token=token&xsec_source=pc_search"
    ) == "https://www.xiaohongshu.com/explore/note_123?xsec_token=token&xsec_source=pc_search"


def test_note_recreation_case_mapping_keeps_before_after_and_second_adjustment():
    case = crawler._map_note_recreation_case({
        "项目名": "露营项目",
        "关键词": "露营帐篷",
        "标题": "原始标题",
        "内容": "原始正文",
        "封面图": "https://cdn.example.com/original.jpg",
        "标题改写.输出结果": "第一次标题",
        "正文改写.输出结果": "第一次正文",
        "封面改写": "https://cdn.example.com/rewrite.jpg",
        "改写打分": 92,
        "二次调整口令": "更轻松一点",
        "二次标题改写": "二次标题",
        "二次正文改写": "二次正文",
        "二次图片改写": "https://cdn.example.com/second.jpg",
    })

    assert case["project_name"] == "露营项目"
    assert case["keyword"] == "露营帐篷"
    assert case["original"] == {
        "title": "原始标题", "body": "原始正文", "images": [{"url": "https://cdn.example.com/original.jpg", "file_token": "", "name": ""}],
        "image_note": "https://cdn.example.com/original.jpg",
    }
    assert case["rewrite"]["title"] == "第一次标题"
    assert case["rewrite"]["images"] == [{"url": "https://cdn.example.com/rewrite.jpg", "file_token": "", "name": ""}]
    assert case["score"] == "92"
    assert case["second_adjustment"]["prompt"] == "更轻松一点"
    assert case["second_adjustment"]["title"] == "二次标题"


@pytest.mark.asyncio
async def test_note_recreation_cases_are_project_scoped_and_require_a_rewritten_cover(monkeypatch):
    fields = [
        {"name": "项目名", "type": "text"}, {"name": "关键词", "type": "text"},
        {"name": "标题", "type": "text"}, {"name": "标题改写", "type": "text"},
        {"name": "封面改写", "type": "attachment"},
    ]
    commands = []

    async def fake_fields(_base_token, _table_id):
        return fields

    async def fake_run(command, timeout_sec=30):
        commands.append(command)
        return {"data": {
            "fields": ["项目名", "关键词", "标题", "标题改写", "封面改写"],
            "data": [
                ["露营项目", "帐篷", "原始标题", "改写标题", [{"file_token": "file_cover", "name": "cover.png"}]],
                ["露营项目", "桌椅", "未改写", "只有文字改写", ""],
            ],
            "record_id_list": ["rec_cover", "rec_text_only"],
            "has_more": False,
        }}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_fields)
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")

    result = await crawler._read_note_recreation_cases("app_demo", "tbl_recreation", "露营项目")

    assert result["cases"] == [{
        "project_name": "露营项目", "keyword": "帐篷", "original": {
            "title": "原始标题", "body": "", "images": [], "image_note": "",
        }, "rewrite": {
            "title": "改写标题", "body": "", "images": [{
                "url": "/api/crawler/note-recreation/attachment?base_token=app_demo&table_id=tbl_recreation&record_id=rec_cover&file_token=file_cover&filename=cover.png",
                "file_token": "file_cover", "name": "cover.png",
            }], "image_note": "file_cover\ncover.png",
        },
        "score": "", "second_adjustment": {"prompt": "", "title": "", "body": "", "images": [], "image_note": ""},
        "note_url": "",
    }]
    filter_json = json.loads(commands[0][commands[0].index("--filter-json") + 1])
    assert filter_json == {"logic": "and", "conditions": [["项目名", "==", "露营项目"]]}


@pytest.mark.asyncio
async def test_note_recreation_attachment_fields_receive_local_preview_urls(monkeypatch):
    fields = [
        {"name": "标题", "type": "text"},
        {"name": "标题改写.输出结果", "type": "text"},
        {"name": "封面附件", "type": "attachment"},
        {"name": "封面改写", "type": "attachment"},
    ]

    async def fake_fields(_base_token, _table_id):
        return fields

    async def fake_run(_command, timeout_sec=30):
        return {"data": {
            "items": [{
                "record_id": "rec_recreation_1",
                "fields": {
                    "标题": "原始标题",
                    "标题改写.输出结果": "改写标题",
                    "封面附件": [{"file_token": "file_original", "name": "original.png"}],
                    "封面改写": [{"file_token": "file_rewrite", "name": "rewrite.png"}],
                },
            }],
            "has_more": False,
        }}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_fields)
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")

    result = await crawler._read_note_recreation_cases("app_demo", "tbl_recreation")

    assert result["cases"][0]["rewrite"]["title"] == "改写标题"
    assert result["cases"][0]["original"]["images"] == [{
        "url": "/api/crawler/note-recreation/attachment?base_token=app_demo&table_id=tbl_recreation&record_id=rec_recreation_1&file_token=file_original&filename=original.png",
        "file_token": "file_original",
        "name": "original.png",
    }]
    assert result["cases"][0]["rewrite"]["images"][0]["url"].endswith("file_token=file_rewrite&filename=rewrite.png")


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
        return [{"name": "note_id", "type": "text"}, {"name": "标题", "type": "text"}, {"name": "笔记封面", "type": "attachment"}]

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
        return [{"name": "笔记ID", "type": "text"}, {"name": "标题", "type": "text"}, {"name": "笔记封面", "type": "attachment"}]

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
    assert calls[0]["payload"] == {"笔记ID": "note-existing", "标题": "old"}
    assert "fields" not in calls[0]["payload"]
    assert "values" not in calls[0]["payload"]
    assert calls[1]["payload"]["rows"] == [["note-new", "new"]]


@pytest.mark.asyncio
async def test_sync_local_to_base_normalizes_nan_select_number_and_user_cells(monkeypatch, tmp_path):
    notes_path = tmp_path / "account-monitor.xlsx"
    notes_path.write_bytes(b"fixture")
    payloads = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        json_arg = cmd[cmd.index("--json") + 1]
        payload_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:]
        payloads.append(json.loads(payload_path.read_text(encoding="utf-8")))
        return {"ok": True, "data": {}}

    async def fake_read_table_field_defs(_base_token, _table_id):
        return [
            {"name": "笔记链接", "type": "text"},
            {"name": "响应率", "type": "text"},
            {"name": "内容类目（标签）", "type": "select", "multiple": True},
            {"name": "粉丝数", "type": "number"},
            {"name": "负责人", "type": "user"},
        ]

    async def fake_read_existing_base_records(_base_token, _table_id, _dedupe_fields):
        return {}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read_table_field_defs)
    monkeypatch.setattr(crawler, "_read_local_rows", lambda *_: [{
        "笔记链接": "https://www.xiaohongshu.com/explore/note-1",
        "响应率": float("nan"),
        "内容类目（标签）": "美食,探店",
        "粉丝数": "1.2万",
        "负责人": "不支持直接写姓名",
    }])
    monkeypatch.setattr(crawler, "_read_existing_base_records", fake_read_existing_base_records)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)

    result = await crawler.sync_local_to_base(crawler.LocalToBaseSyncRequest(
        base_token="app123",
        table_id="tbl123",
        data_type="notes",
        file_path=str(notes_path),
    ))

    assert result["created"] == 1
    assert payloads[0]["rows"] == [[
        "https://www.xiaohongshu.com/explore/note-1",
        None,
        ["美食", "探店"],
        12000,
        None,
    ]]


def test_normalize_base_cell_uses_cli_shapes_for_text_and_select_fields():
    assert crawler._normalize_base_cell_value(123456, {"type": "text"}) == "123456"
    assert crawler._normalize_base_cell_value("美食", {"type": "select"}) == "美食"
    assert crawler._normalize_base_cell_value(
        "美食,探店", {"type": "multi_select"}
    ) == ["美食", "探店"]


@pytest.mark.asyncio
async def test_search_sync_rejects_rows_without_current_keyword(monkeypatch, tmp_path):
    notes_path = tmp_path / "search.json"
    notes_path.write_text(json.dumps([
        {"note_id": "old-record", "title": "历史无关键词内容", "source_keyword": ""},
        {"note_id": "wanted", "title": "许嵩演唱会现场", "source_keyword": "许嵩演唱会"},
    ], ensure_ascii=False), encoding="utf-8")
    payloads = []

    async def fake_run_lark_cli(cmd, timeout_sec=30):
        json_arg = cmd[cmd.index("--json") + 1]
        payload_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:]
        payloads.append(json.loads(payload_path.read_text(encoding="utf-8")))
        return {"ok": True, "data": {}}

    async def fake_read_table_field_defs(_base_token, _table_id):
        return [{"name": "笔记ID", "type": "text"}, {"name": "标题", "type": "text"}]

    async def fake_read_existing_base_records(*_args):
        return {}

    monkeypatch.setattr(crawler, "_read_table_field_defs", fake_read_table_field_defs)
    monkeypatch.setattr(crawler, "_read_existing_base_records", fake_read_existing_base_records)
    monkeypatch.setattr(crawler, "_find_lark_cli", lambda: "lark-cli")
    monkeypatch.setattr(crawler, "_run_lark_cli", fake_run_lark_cli)

    result = await crawler.sync_local_to_base(crawler.LocalToBaseSyncRequest(
        base_token="base",
        table_id="table",
        data_type="notes",
        crawler_type_hint="search",
        source_keyword="许嵩演唱会",
        file_path=str(notes_path),
    ))

    assert result["created"] == 1
    assert payloads[0]["rows"] == [["wanted", "许嵩演唱会现场"]]


def test_sentiment_comment_enrichment_keeps_note_and_comment_likes_separate():
    rows = crawler._enrich_sentiment_comment_rows(
        [{"note_id": "note-1", "content": "价格高", "like_count": "7"}],
        [{
            "note_id": "note-1",
            "title": "演唱会攻略",
            "note_url": "https://www.xiaohongshu.com/explore/note-1",
            "liked_count": "321",
            "comment_count": "45",
        }],
        "",
    )

    assert rows[0]["笔记链接"] == "https://www.xiaohongshu.com/explore/note-1"
    assert rows[0]["笔记标题"] == "演唱会攻略"
    assert rows[0]["点赞数"] == "321"
    assert rows[0]["评论总数"] == "45"
    assert rows[0]["comment_like_count"] == "7"
