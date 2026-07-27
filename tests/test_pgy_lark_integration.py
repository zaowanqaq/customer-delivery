# -*- coding: utf-8 -*-
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from api.routers import crawler
from api.schemas import PgyKolRunRequest, PgyLoginRequest
from tools import pgy_automation


def test_lark_json_arg_uses_relative_temp_file_and_cleans_up():
    payload = {"fields": ["达人昵称"], "rows": [["数字生命卡兹克"]]}

    with crawler._lark_json_arg(payload) as json_arg:
        assert json_arg.startswith("@./") or json_arg.startswith("@.\\")
        temp_path = Path(crawler.__file__).resolve().parents[2] / json_arg[3:].replace("\\", "/")
        assert temp_path.exists()
        assert json.loads(temp_path.read_text(encoding="utf-8")) == payload

    assert not temp_path.exists()


def test_pgy_summary_rows_map_core_read_imp_to_existing_median_fields():
    summary = {
        "nickname": "数字生命卡兹克",
        "red_id": "wzglyay2023",
        "blogger_detail": {
            "userId": "62c98736000000001501e075",
            "name": "数字生命卡兹克",
            "redId": "wzglyay2023",
        },
        "target_metrics": {
            "data_date": "",
            "exposure_count": "",
            "read_count": "",
            "imp_median": "",
            "read_median": "",
        },
        "propagation_performance": {
            "core_data": {
                "sumData": {
                    "imp": 132139,
                    "read": 20753,
                    "dateKey": "2026-05-20",
                }
            }
        },
    }

    row = crawler._pgy_summary_to_rows(summary, "downloads/pgy/数字生命卡兹克")[0]

    assert row["blogger_homepage_url"] == "https://www.xiaohongshu.com/user/profile/62c98736000000001501e075"
    assert row["exposure_count"] == 132139
    assert row["read_count"] == 20753
    assert row["data_date"] == "2026-05-20"

    values = crawler._pgy_row_to_values(row, ["博主主页", "曝光中位数", "阅读中位数"])
    assert values == [
        "https://www.xiaohongshu.com/user/profile/62c98736000000001501e075",
        132139,
        20753,
    ]


def test_pgy_summary_rows_map_to_customer_creator_selection_fields():
    summary = {
        "nickname": "数字生命卡兹克",
        "red_id": "wzglyay2023",
        "blogger_detail": {
            "userId": "62c98736000000001501e075",
            "name": "数字生命卡兹克",
            "redId": "wzglyay2023",
            "fansCount": 12345,
            "likeCollectCountInfo": 67890,
            "businessNoteCount": 8,
            "picturePrice": 1000,
            "videoPrice": 2000,
        },
        "propagation_performance": {
            "data_summary": {"dateKey": "2026-05-20", "noteNumber": 88},
            "core_data": {"sumData": {"imp": 132139, "read": 20753, "dateKey": "2026-05-20"}},
            "notes_rate": {"interactionMedian": 321, "likeMedian": 111, "collectMedian": 22, "commentMedian": 3},
        },
    }

    row = crawler._pgy_summary_to_rows(summary, "downloads/pgy/数字生命卡兹克")[0]
    values = crawler._pgy_row_to_values(row, [
        "目标/推荐博主",
        "推荐排名",
        "达人昵称",
        "小红书号",
        "主页链接",
        "蒲公英主页链接",
        "粉丝数",
        "日常笔记曝光中位数",
        "日常笔记阅读中位数",
        "日常笔记互动中位数",
        "日常笔记中位点赞量",
        "日常笔记中位收藏量",
        "日常笔记中位评论量",
        "最新笔记更新时间",
        "采集博主数据日期",
    ])

    assert values[:7] == [
        "目标达人",
        0,
        "数字生命卡兹克",
        "wzglyay2023",
        "https://www.xiaohongshu.com/user/profile/62c98736000000001501e075",
        "https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/62c98736000000001501e075",
        12345,
    ]
    assert values[7:13] == [132139, 20753, 321, 111, 22, 3]
    assert values[14] == "2026-05-20"


def test_pgy_summary_only_syncs_similar_creators_after_detail_fetch():
    summary = {
        "nickname": "目标达人",
        "blogger_detail": {"userId": "target-1", "name": "目标达人"},
        "similar_creators": [
            {"nickname": "仅推荐卡片", "_user_id": "similar-1", "detail_fetched": False},
            {
                "nickname": "已抓完整详情",
                "_user_id": "similar-2",
                "detail_fetched": True,
                "fans_count": 12345,
            },
        ],
    }

    rows = crawler._pgy_summary_to_rows(summary, "downloads/pgy/目标达人")

    assert [row["nickname"] for row in rows] == ["目标达人", "已抓完整详情"]
    assert rows[1]["fans_count"] == 12345


def test_pgy_detail_attempts_request_daily_and_business_note_metrics():
    attempts = pgy_automation.make_detail_attempts("user-1")

    assert attempts["daily_notes_rate"][0][2]["business"] == 0
    assert attempts["business_notes_rate"][0][2]["business"] == 1
    assert attempts["daily_core_data"][0][2]["business"] == "0"
    assert attempts["business_core_data"][0][2]["business"] == "1"


def test_pgy_note_report_row_maps_single_note_metrics():
    row = pgy_automation.normalize_pgy_note_grid_row(
        "note-123",
        {
            "note": {
                "lines": ["一篇合作笔记", "笔记ID：note-123"],
                "links": ["https://www.xiaohongshu.com/explore/note-123"],
            },
            "kol": {
                "lines": ["测试达人", "小红书号：red-123"],
                "links": ["https://www.xiaohongshu.com/user/profile/user-123"],
            },
            "notePublishTime": {"lines": ["2026-07-18 10:30"]},
            "collectionType": {"lines": ["母婴", "测评"]},
            "impNum": {"lines": ["1.2万"]},
            "readNum": {"lines": ["3,456"]},
            "likeNum": {"lines": ["120"]},
            "favNum": {"lines": ["34"]},
            "cmtNum": {"lines": ["5"]},
            "shareNum": {"lines": ["6"]},
            "followCnt": {"lines": ["7"]},
        },
    )

    assert row["title"] == "一篇合作笔记"
    assert row["author_nickname"] == "测试达人"
    assert row["exposure_count"] == 12000
    assert row["read_count"] == 3456
    assert row["liked_count"] == 120
    assert row["collected_count"] == 34
    assert row["comment_count"] == 5
    assert row["share_count"] == 6
    assert row["pgy_follow_count"] == 7
    assert row["tag_list"] == ["母婴", "测评"]


def test_collaboration_note_merge_prefers_pgy_values_and_keeps_xhs_fallbacks():
    merged = crawler._merge_pgy_note_data(
        {
            "note_id": "note-123",
            "title": "小红书标题",
            "author_user_id": "user-123",
            "liked_count": 10,
            "collected_count": 2,
            "comment_count": 1,
            "share_count": 3,
            "cover": "https://example.com/cover.jpg",
        },
        {
            "note_id": "note-123",
            "title": "蒲公英标题",
            "liked_count": 20,
            "collected_count": 4,
            "comment_count": 0,
            "share_count": 6,
            "exposure_count": 2000,
            "read_count": 1000,
            "pgy_note_source": "蒲公英笔记报告",
        },
    )

    assert merged["title"] == "蒲公英标题"
    assert merged["liked_count"] == 20
    assert merged["comment_count"] == 0
    assert merged["exposure_count"] == 2000
    assert merged["read_count"] == 1000
    assert merged["author_user_id"] == "user-123"
    assert merged["cover"] == "https://example.com/cover.jpg"
    assert merged["pgy_note_source"] == "蒲公英笔记报告"


@pytest.mark.asyncio
async def test_pgy_note_fetch_uses_headless_saved_profile_without_cdp(monkeypatch):
    captured_args = []

    async def fake_run(args, timeout_sec=240):
        captured_args.extend(args)
        return {
            "status": "ok",
            "requested_count": 1,
            "matched_count": 1,
            "notes": [{"note_id": "note-123", "read_count": 10}],
            "returncode": 0,
        }

    monkeypatch.setattr(crawler, "_pgy_cdp_available", lambda: False)
    monkeypatch.setattr(crawler, "_run_pgy_automation", fake_run)

    result = await crawler._fetch_pgy_note_data(["note-123", "note-123"])

    assert captured_args[:3] == ["run-note-data", "--note-ids", "note-123"]
    assert "--headless" in captured_args
    assert result["matched_count"] == 1


def test_pgy_no_result_uses_page_text_markers():
    assert pgy_automation.has_no_kol_result("搜索完成，暂无结果")
    assert pgy_automation.has_no_kol_result("暂未找到相关博主")
    assert not pgy_automation.has_no_kol_result("已找到 3 位相关博主")


def test_pgy_response_classifier_uses_business_request_parameter():
    class Request:
        post_data = '{"business":"1"}'

    class Response:
        url = "https://pgy.xiaohongshu.com/api/pgy/kol/data/core_data"
        request = Request()

    assert pgy_automation.classify_api_response(Response()) == "business_core_data"


def test_pgy_profile_and_metric_groups_map_to_creator_selection_columns():
    api_data = {
        "blogger_detail": {
            "data": {
                "contentTags": [
                    {"taxonomy1Tag": "美妆"},
                    {"taxonomy1Tag": "时尚"},
                ],
                "industryTag": "服装配饰",
            }
        },
        "daily_core_data": {"data": {"sumData": {"imp": 120, "read": 12}}},
        "daily_notes_rate": {"data": {"interactionMedian": 3, "likeMedian": 4}},
        "business_core_data": {"data": {"sumData": {"imp": 320, "read": 32}}},
        "business_notes_rate": {"data": {"interactionMedian": 8, "likeMedian": 9}},
    }

    metrics = pgy_automation.flatten_detail_metrics(api_data)
    values = crawler._pgy_row_to_values(metrics, [
        "内容类目（标签）",
        "合作行业",
        "日常笔记曝光中位数",
        "日常笔记互动中位数",
        "合作笔记曝光中位数",
        "合作笔记互动中位数",
    ])

    assert values == ["美妆,时尚", "服装配饰", 120, 3, 320, 8]


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
    async def fake_wait_for_cdp(timeout_sec=15.0, process=None):
        assert process is not None
        return True

    monkeypatch.setattr(crawler, "_wait_for_pgy_cdp", fake_wait_for_cdp)
    monkeypatch.setattr(crawler, "_open_url_in_cdp", lambda *_args: True)

    result = await crawler.pgy_login(PgyLoginRequest(keep_open=True, timeout_ms=30000))

    assert "--executable-path" in captured_cmd
    assert str(browser_path) in captured_cmd
    assert "--channel" not in captured_cmd
    assert result["opened_url"] is True


@pytest.mark.asyncio
async def test_pgy_login_keep_open_reuses_cdp_and_opens_login_page(monkeypatch):
    opened = []

    monkeypatch.setattr(crawler, "_pgy_cdp_available", lambda: True)
    monkeypatch.setattr(crawler, "_open_url_in_cdp", lambda endpoint, url: opened.append((endpoint, url)) or True)
    monkeypatch.setattr(crawler, "_focus_detected_browser", lambda: True)

    result = await crawler.pgy_login(PgyLoginRequest(keep_open=True, timeout_ms=30000))

    assert result["status"] == "login_window_opened"
    assert result["url"] == crawler.PGY_LOGIN_URL
    assert result["opened_url"] is True
    assert result["browser_focused"] is True
    assert opened == [(crawler.PGY_CDP_ENDPOINT, crawler.PGY_LOGIN_URL)]


@pytest.mark.asyncio
async def test_run_lark_cli_preserves_non_utf8_error_text():
    script = "import sys; sys.stderr.buffer.write('错误'.encode('gb18030')); sys.exit(2)"

    with pytest.raises(HTTPException) as exc_info:
        await crawler._run_lark_cli([sys.executable, "-c", script], timeout_sec=10)

    assert "错误" in exc_info.value.detail
