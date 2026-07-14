# -*- coding: utf-8 -*-
from pathlib import Path


OPS_CONFIG_HTML = Path(__file__).resolve().parents[1] / "api" / "webui" / "ops_config.html"


def test_creator_selection_tab_embeds_ai_profile_screening_controls():
    html = OPS_CONFIG_HTML.read_text(encoding="utf-8")

    assert 'id="creator_selection_pgy_panel"' in html
    assert 'id="creator_selection_ai_panel"' in html
    assert "AI 主页初筛" in html
    assert 'id="creator_screening_file"' in html
    assert 'accept=".csv,.xlsx,.xls"' in html
    assert 'onchange="handleCreatorScreeningFileChange()"' in html
    assert 'id="creator_screening_requirement"' in html
    assert "data.siliconflow_configured" in html
    assert "details[open][data-screening-row]" in html
    assert "data-screening-row=" in html
    ai_panel = html.split('id="creator_selection_ai_panel"', 1)[1].split('</section>', 1)[0]
    assert "openCreatorScreeningLogin()" not in ai_panel
    assert "打开小红书登录浏览器" not in ai_panel
    assert "启动 AI 初筛" in html
    assert ">导入达人库<" not in html
    assert "/api/creator-screening/import" in html
    assert "/api/creator-screening/jobs" in html


def test_creator_selection_ai_result_table_has_all_customer_columns_and_statuses():
    html = OPS_CONFIG_HTML.read_text(encoding="utf-8")

    for label in ("序号", "是否符合筛选要求", "达人昵称", "博主id", "主页链接", "ip地", "达人类型", "达人价格"):
        assert label in html
    for status in ("符合", "不符合", "待人工确认", "异常"):
        assert status in html
    assert "主页链接为必填项；博主ID可选" in html
