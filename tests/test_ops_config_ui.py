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

    assert "base_token: payload.sync_base_token" in html
    assert "table_id: payload.account_filter_table_id" in html
    assert "/api/crawler/account-monitor/status" in html
    assert 'waitCrawlerIdleThenAutoSync("viral")' in html
    assert "AUTO_SYNC_POLL_INTERVAL_MS" in html


def test_customer_feedback_copy_and_navigation_are_updated():
    html = _ops_config_text()

    expected_nav = [
        "项目大盘",
        "平台爆款检索",
        "爆款笔记二创",
        "达人智能圈选",
        "账号内容监测",
        "笔记数据监测",
        "笔记舆情监控",
    ]
    positions = [html.index(f">{label}<") for label in expected_nav]

    assert positions == sorted(positions)
    assert "<h2>小红书营销一体化平台</h2>" in html
    assert "<h2>小红书营销项目一体化平台</h2>" not in html
    assert "围绕项目执行打造的营销工作台，用于串联策略判断、内容检索、达人筛选、数据同步与持续监控，承接艾莉芬特在小红书整合营销、AI 内容营销、流量助推、KOC 与达人投放上的组合能力。" in html
    assert "这是一套围绕项目执行打造的营销工作台" not in html
    assert "用于项目关键词爆款检索，多个关键词会依次抓取，抓取后在步骤4写入多维表。" not in html
    assert "和抓取流程并排查看当前采集结果，支持关键词筛选、标题/正文搜索、瀑布流预览与详情查看。" not in html
    assert "保存传播表现、粉丝分析" not in html
    assert "工作台用于保存、切换或加入已有项目" in html
    assert "项目检索栏" in html
    assert "一键新建项目" in html
    assert "Base 链接 / Token（必填）" in html
    assert "爆款笔记二创" in html
    assert "仅展示已上传“封面改写”的记录。" in html
    assert "/api/crawler/note-recreation/cases" in html
    assert "recreationImageUrl" in html
    assert "recreation-note-card" in html
    assert "recreation-cover" in html
    assert "本页不调用 AI" in html
    assert "笔记链接（逗号或换行）" in html
    assert "风险类型分组" in html
    assert "新增风险类型" in html
    assert "同步规则到多维表格" in html
    assert "评论区分析" not in html
    assert "/api/crawler/sentiment-monitor/start" in html
    assert "/api/crawler/sentiment-monitor/sync-rules" in html
    assert "需处理表示该项会影响对应功能，需要按右侧处理办法优先处理" in html


def test_existing_project_link_join_and_multi_keyword_copy_are_present():
    html = _ops_config_text()

    assert 'id="existing_project_link"' in html
    assert 'id="existing_project_name"' in html
    assert "加入其他项目 - 项目名" in html
    assert "加入其他项目 - 项目链接" in html
    assert html.index('id="existing_project_name"') < html.index('id="existing_project_link"')
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
    assert "const projectName = manualName || baseName || slotName || baseToken;" in html
    assert "baseName || projectNameInput.value.trim()" not in html


def test_cookie_login_is_the_only_visible_login_mode():
    html = _ops_config_text()

    assert '<option value="cookie">Cookie 登录</option>' in html
    assert 'value="qrcode"' not in html
    assert "扫码登录" not in html
    assert "openXhsLoginBrowser()" in html
    assert "/api/crawler/xhs/login-browser" in html
    assert "打开小红书登录浏览器" in html
    assert "先在弹出的浏览器里登录小红书" in html
    assert "loadCookiesFromBrowser()" in html
    assert "/api/crawler/browser-cookies" in html
    assert "从已登录浏览器读取 Cookie" in html


def test_project_overview_uses_single_full_width_panel_and_hot_content_compass():
    html = _ops_config_text()

    assert "grid-template-columns: 1fr; gap: 20px; margin-bottom: 22px;" in html
    assert 'class="overview-side"' not in html
    assert "选号罗盘" not in html
    assert "<h2>爆款罗盘</h2>" in html


def test_project_binding_uses_customer_table_names():
    html = _ops_config_text()

    assert "爆款评论 -> 笔记舆情监控表" in html
    assert "步骤5合作笔记 -> 笔记数据监测表" in html
    assert "步骤5合作评论 -> 合作笔记舆情监控表" in html
    assert 'collab_comments_table_name: document.getElementById("collab_comments_table_name").value.trim()' in html
    assert "爆款评论表" not in html
    assert "合作监控表" not in html
    assert "合作评论表" not in html


def test_collaboration_monitor_keeps_latest_twenty_notes_without_exposing_a_count_control():
    html = _ops_config_text()

    assert "每轮每个账号抓取篇数" not in html
    assert "按客户提供的合作博主名单持续监控，不依赖默认账号或开发机数据。" not in html
    assert "collab_notes_per_creator: 20" in html


def test_sample_account_file_import_controls_are_present():
    html = _ops_config_text()

    assert "第一阶段使用小红书登录态抓取笔记" in html
    assert "第二阶段使用蒲公英登录态" in html
    assert "表头固定为「主页链接」" in html
    assert 'id="sample_accounts_file"' in html
    assert 'accept=".txt,.csv,.xlsx,.xls"' in html
    assert "importSampleAccountsFile()" in html
    assert "/api/crawler/import-sample-accounts" in html
    assert "mergeSampleAccounts" in html
    assert 'id="account_monitor_mode"' not in html
    assert "在“找博主”中判断该达人是否开通蒲公英主页" in html
    assert 'account_monitor_mode: "auto"' in html
    assert "/api/crawler/account-monitor/start" in html
    assert "downloadAccountMonitorTemplate()" in html
    assert "账号内容监测_主页链接模板.csv" in html
    assert "下载账号内容监测表" not in html


def test_login_issues_open_actionable_prompts_in_the_matching_tabs():
    html = _ops_config_text()

    assert 'id="workflow_alert"' in html
    assert 'tabId: "tab-step2"' in html
    assert 'tabId = "tab-step3"' in html
    assert "pgy_login_required" in html
    assert "pgy_login_accounts" in html
    assert "statusData.login_required" in html
    assert "打开蒲公英登录窗口" in html
    assert "打开小红书登录浏览器" in html
    assert "onAction: pgyLogin" in html
    assert "从蒲公英断点继续" in html
    assert "/api/crawler/account-monitor/resume-pgy" in html
    assert "不会重复抓取小红书" in html
    assert '"tab-step2"' in html
    assert "账号内容监测第一阶段需要使用小红书登录态" in html
    assert 'id="account_monitor_start_btn"' in html
    assert "从蒲公英断点继续" in html


def test_file_preview_routes_api_requests_to_local_server():
    html = _ops_config_text()

    assert 'window.location.protocol === "file:" ? "http://127.0.0.1:8081" : ""' in html
    assert 'input.startsWith("/api/")' in html
