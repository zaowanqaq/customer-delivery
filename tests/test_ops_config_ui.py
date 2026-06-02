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
