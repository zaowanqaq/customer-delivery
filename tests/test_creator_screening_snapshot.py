# -*- coding: utf-8 -*-
import pytest

from tools.xhs_profile_snapshot import capture_visible_profile


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

    async def goto(self, url, wait_until, timeout):
        self.url = url

    async def content(self):
        return "登录 验证码" if self.login_required else "主页内容"

    async def evaluate(self, script):
        self.scroll_count += 1

    async def wait_for_timeout(self, timeout):
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
    assert len(snapshot.screenshot_paths) == 2
    assert page.scroll_count == 3
    assert page.detail_navigation_attempts == 0


@pytest.mark.asyncio
async def test_login_page_returns_exception_snapshot(tmp_path):
    snapshot = await capture_visible_profile(
        FakePage(login_required=True),
        "https://www.xiaohongshu.com/user/profile/a",
        tmp_path,
    )

    assert snapshot.status == "异常"
    assert "登录" in snapshot.error
