# -*- coding: utf-8 -*-
from pathlib import Path


OPS_CONFIG_HTML = Path(__file__).resolve().parents[1] / "api" / "webui" / "ops_config.html"


def _ops_config_text() -> str:
    return OPS_CONFIG_HTML.read_text(encoding="utf-8")


def test_customer_ui_hides_local_save_and_manual_sync_controls():
    html = _ops_config_text()

    assert 'id="save_option" class="v1-hidden"' in html
    assert 'id="enable_media" class="v1-hidden"' in html
    assert 'id="sync_file_path" class="v1-hidden"' in html
    assert 'id="sync_limit" class="v1-hidden"' in html
    assert 'data-internal-manual-sync="true"' in html
    assert 'data-internal-pgy-manual-sync="true"' in html
    assert "保留手动同步代码" in html


def test_step2_and_step3_start_flows_auto_sync_after_crawler_finishes():
    html = _ops_config_text()

    assert 'waitCrawlerIdleThenAutoSync("sample")' in html
    assert 'waitCrawlerIdleThenAutoSync("viral")' in html
    assert "AUTO_SYNC_POLL_INTERVAL_MS" in html


def test_customer_feedback_copy_and_navigation_are_updated():
    html = _ops_config_text()

    expected_nav = [
        "项目大盘",
        "平台爆款检索",
        "达人智能圈选",
        "账号内容监测",
        "笔记数据监控",
        "笔记舆情监控",
    ]
    positions = [html.index(f">{label}<") for label in expected_nav]

    assert positions == sorted(positions)
    assert "小红书营销项目一体化平台" in html
    assert "工作台汇总，用于保存项目配置，新旧项目不重合，独立项目独自运营" in html
    assert "项目检索栏" in html
    assert "一键新建项目" in html
    assert "Base 链接 / Token（必填）" in html
    assert "二级评论就是楼中楼评论" in html
    assert "需处理表示该项会影响对应功能，需要按右侧处理办法优先处理" in html
