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
        "笔记标题", "笔记内容", "笔记tag", "点赞", "收藏数", "分享数", "评论数",
        "阅读量", "曝光量", "总互动数据（赞+藏+评，不算分享）", "采集数据时间", "封面附件",
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
    assert sentiment_fields[:5] == ["项目名", "笔记链接", "笔记标题", "评论总数", "舆情风险"]
    risk_field = next(field for field in crawler._sentiment_monitor_fields() if field["name"] == "舆情风险")
    assert risk_field == {
        "name": "舆情风险",
        "type": "formula",
        "expression": "\"\"",
        "description": "笔记舆情监控自动汇总的风险类型",
    }
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
        assert captured["start_request"].save_option.value == "csv"
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
        {"封面附件": "fld5NvqX7K"},
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
