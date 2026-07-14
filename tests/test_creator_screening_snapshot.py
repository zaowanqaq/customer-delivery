# -*- coding: utf-8 -*-
import json

import pytest

from api.schemas.creator_screening import CreatorCandidateInput
from api.services import creator_screening
from tools import xhs_profile_snapshot
from tools.xhs_profile_snapshot import (
    SCREENING_PAGE_NAME,
    _get_or_create_screening_page,
    capture_visible_profile,
    extract_profile_ip,
)


class FakeLocator:
    def __init__(self, page):
        self.page = page

    async def inner_text(self, timeout=0):
        return self.page.visible_text


class FakePage:
    def __init__(self, visible_text="", login_required=False):
        self.visible_text = visible_text
        self.login_required = login_required
        self.scroll_count = 0
        self.screenshots = []
        self.detail_navigation_attempts = 0
        self.keyboard = self

    async def goto(self, url, wait_until, timeout):
        self.url = url

    async def content(self):
        return "手机号登录 获取验证码" if self.login_required else "主页内容"

    async def evaluate(self, script):
        self.scroll_count += 1

    async def wait_for_timeout(self, timeout):
        return None

    async def press(self, key):
        return None

    def locator(self, selector):
        assert selector == "body"
        return FakeLocator(self)

    async def screenshot(self, path, full_page=False):
        self.screenshots.append((path, full_page))
        with open(path, "wb") as output:
            output.write(b"png")


@pytest.mark.asyncio
async def test_visible_profile_snapshot_limits_scrolls_and_screenshots(tmp_path):
    page = FakePage(visible_text="简介 IP属地：浙江 笔记：线下打卡")

    snapshot = await capture_visible_profile(page, "https://www.xiaohongshu.com/user/profile/a", tmp_path)

    assert snapshot.status == "ok"
    assert snapshot.ip_location == "浙江"
    assert len(snapshot.screenshot_paths) == 1
    assert page.scroll_count == 2
    assert page.detail_navigation_attempts == 0


@pytest.mark.asyncio
async def test_login_page_returns_manual_review_snapshot(tmp_path):
    snapshot = await capture_visible_profile(
        FakePage(login_required=True),
        "https://www.xiaohongshu.com/user/profile/a",
        tmp_path,
    )

    assert snapshot.status == "待人工确认"
    assert snapshot.ip_location == ""
    assert len(snapshot.screenshot_paths) == 1


@pytest.mark.asyncio
async def test_profile_with_visible_login_copy_is_not_mistaken_for_login_page(tmp_path):
    class ProfilePage(FakePage):
        async def content(self):
            return "登录探索更多内容 体坛周报 小红书号：6301714171 IP属地：重庆"

    snapshot = await capture_visible_profile(
        ProfilePage(visible_text="体坛周报 小红书号：6301714171 IP属地：重庆"),
        "https://www.xiaohongshu.com/user/profile/a",
        tmp_path,
    )

    assert snapshot.status == "ok"
    assert snapshot.ip_location == "重庆"


@pytest.mark.asyncio
async def test_login_overlay_is_dismissed_before_profile_is_marked_unavailable(tmp_path):
    class LoginOverlayPage(FakePage):
        def __init__(self):
            super().__init__(visible_text="登录探索更多内容")
            self.overlay_open = True

        async def content(self):
            return "登录 验证码" if self.overlay_open else "主页内容"

        async def press(self, key):
            assert key == "Escape"
            self.overlay_open = False
            self.visible_text = "体坛周报 小红书号：6301714171 IP属地：重庆"

    snapshot = await capture_visible_profile(
        LoginOverlayPage(),
        "https://www.xiaohongshu.com/user/profile/a",
        tmp_path,
    )

    assert snapshot.status == "ok"
    assert snapshot.ip_location == "重庆"


@pytest.mark.asyncio
async def test_login_overlay_uses_visible_close_control_when_escape_is_ignored(tmp_path):
    class LoginOverlayPage(FakePage):
        def __init__(self):
            super().__init__(visible_text="登录探索更多内容")
            self.overlay_open = True

        async def content(self):
            return "手机号登录 获取验证码" if self.overlay_open else "主页内容"

        async def evaluate(self, script):
            if "querySelectorAll" in script:
                self.overlay_open = False
                self.visible_text = "体坛周报 小红书号：6301714171 IP属地：重庆"
                return True
            return await super().evaluate(script)

        async def press(self, key):
            raise AssertionError("登录浮层有关闭按钮时不应依赖 Escape")

    snapshot = await capture_visible_profile(
        LoginOverlayPage(),
        "https://www.xiaohongshu.com/user/profile/a",
        tmp_path,
    )

    assert snapshot.status == "ok"
    assert snapshot.ip_location == "重庆"


@pytest.mark.asyncio
async def test_screening_reuses_one_marked_browser_tab():
    class FakeScreeningPage:
        def __init__(self):
            self.name = ""

        async def evaluate(self, script):
            if script == "window.name":
                return self.name
            assert "window.name =" in script
            self.name = SCREENING_PAGE_NAME

    class FakeContext:
        def __init__(self):
            self.pages = []
            self.new_page_calls = 0

        async def new_page(self):
            self.new_page_calls += 1
            page = FakeScreeningPage()
            self.pages.append(page)
            return page

    context = FakeContext()
    first_page = await _get_or_create_screening_page(context)
    second_page = await _get_or_create_screening_page(context)

    assert first_page is second_page
    assert context.new_page_calls == 1


def test_profile_ip_is_extracted_from_profile_header_text():
    assert extract_profile_ip("体育周报 小红书号：6301714171 IP属地：重庆 中国综合类体育类报纸") == "重庆"


@pytest.mark.asyncio
async def test_snapshot_subprocess_inherits_project_pythonpath(monkeypatch, tmp_path):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            payload = {"profile_url": "https://www.xiaohongshu.com/user/profile/a", "status": "ok"}
            return json.dumps(payload).encode("utf-8"), b""

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProcess()

    monkeypatch.setattr(creator_screening, "temp_dir", lambda: tmp_path)
    monkeypatch.setattr(creator_screening.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    snapshot = await creator_screening.collect_profile_snapshot(
        CreatorCandidateInput(index=1, profile_url="https://www.xiaohongshu.com/user/profile/a"),
        "job-1",
    )

    assert snapshot.status == "ok"
    assert captured["env"]["PYTHONPATH"].split(";")[0] == str(creator_screening.Path(creator_screening.__file__).resolve().parents[2])


def test_snapshot_uses_the_workbench_xhs_login_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(xhs_profile_snapshot, "browser_data_dir", lambda: tmp_path)
    monkeypatch.setattr(xhs_profile_snapshot.config, "USER_DATA_DIR", "%s_user_data_dir")
    monkeypatch.setattr(xhs_profile_snapshot.config, "PLATFORM", "xhs")

    assert xhs_profile_snapshot.shared_xhs_login_profile_dir() == tmp_path / "cdp_xhs_user_data_dir"


def test_snapshot_configures_utf8_stdout_when_supported(monkeypatch):
    calls = []

    class FakeStdout:
        def reconfigure(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(xhs_profile_snapshot.sys, "stdout", FakeStdout())

    xhs_profile_snapshot.configure_utf8_stdout()

    assert calls == [{"encoding": "utf-8"}]
