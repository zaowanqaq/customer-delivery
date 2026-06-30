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
    assert "工作台用于保存、切换或加入已有项目" in html
    assert "项目检索栏" in html
    assert "一键新建项目" in html
    assert "Base 链接 / Token（必填）" in html
    assert "二级评论就是楼中楼评论" in html
    assert "需处理表示该项会影响对应功能，需要按右侧处理办法优先处理" in html


def test_existing_project_link_join_and_multi_keyword_copy_are_present():
    html = _ops_config_text()

    assert 'id="existing_project_link"' in html
    assert 'id="existing_project_name"' in html
    assert "joinExistingProject()" in html
    assert "function extractBaseToken" in html
    assert "fetchBaseInfo" in html
    assert "/api/crawler/base-info" in html
    assert "function bindProjectTablesFromList" in html
    assert "已加入已有项目" in html
    assert "多个关键词用英文逗号分隔" in html
    assert "搜索关键词（单个）" not in html


def test_join_existing_project_does_not_fallback_to_stale_project_name():
    html = _ops_config_text()

    assert "const slotName = slotInput.value.trim();" in html
    assert "const manualName = (document.getElementById(\"existing_project_name\")?.value || \"\").trim();" in html
    assert "const projectName = baseName || manualName || slotName || baseToken;" in html
    assert "baseName || projectNameInput.value.trim()" not in html


def test_cookie_login_is_the_only_visible_login_mode():
    html = _ops_config_text()

    assert '<option value="cookie">Cookie 登录</option>' in html
    assert 'value="qrcode"' not in html
    assert "扫码登录" not in html
    assert "loadCookiesFromBrowser()" in html
    assert "/api/crawler/browser-cookies" in html
    assert "从已登录浏览器读取 Cookie" in html


def test_project_overview_uses_single_full_width_panel_and_hot_content_compass():
    html = _ops_config_text()

    assert "grid-template-columns: 1fr; gap: 20px; margin-bottom: 22px;" in html
    assert 'class="overview-side"' not in html
    assert "选号罗盘" not in html
    assert "<h2>爆款罗盘</h2>" in html


def test_sample_account_file_import_controls_are_present():
    html = _ops_config_text()

    assert 'id="sample_accounts_file"' in html
    assert 'accept=".txt,.csv,.xlsx,.xls"' in html
    assert "importSampleAccountsFile()" in html
    assert "/api/crawler/import-sample-accounts" in html
    assert "mergeSampleAccounts" in html


def test_file_preview_routes_api_requests_to_local_server():
    html = _ops_config_text()

    assert 'window.location.protocol === "file:" ? "http://127.0.0.1:8081" : ""' in html
    assert 'input.startsWith("/api/")' in html
