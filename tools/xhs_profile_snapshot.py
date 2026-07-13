# -*- coding: utf-8 -*-
"""Collect only the currently visible information from a Xiaohongshu profile page."""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright

import config
from api.services.creator_screening import ProfileSnapshot
from config.runtime_paths import browser_data_dir
from tools.browser_launcher import BrowserLauncher


LOGIN_MARKERS = ("登录", "验证码", "滑动验证", "请先登录")


def shared_xhs_login_profile_dir() -> Path:
    return browser_data_dir() / f"cdp_{config.USER_DATA_DIR % config.PLATFORM}"


def configure_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


async def _looks_like_login(page: Page) -> bool:
    visible_text = await page.locator("body").inner_text(timeout=5_000)
    if "小红书号" in visible_text or "IP属地" in visible_text:
        return False
    content = await page.content()
    return any(marker in content for marker in LOGIN_MARKERS)


def extract_profile_ip(text: str) -> str:
    match = re.search(r"(?:IP属地|IP 地|IP所在地)\s*[：:]?\s*([^\s|｜，,。]{1,20})", text)
    return match.group(1).strip() if match else ""


async def _save_at_most_two_screenshots(page: Page, output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, full_page in enumerate((False, True), start=1):
        path = output_dir / f"profile-{index}.png"
        await page.screenshot(path=str(path), full_page=full_page)
        paths.append(str(path))
    return paths


async def capture_visible_profile(page: Page, profile_url: str, output_dir: Path) -> ProfileSnapshot:
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30_000)
        if await _looks_like_login(page):
            return ProfileSnapshot(profile_url=profile_url, status="异常", error="主页要求登录或出现验证码")
        profile_header_text = (await page.locator("body").inner_text(timeout=5_000)).strip()
        profile_ip_location = extract_profile_ip(profile_header_text)
        for _ in range(3):
            await page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight * 0.8))")
            await page.wait_for_timeout(500)
        text = (await page.locator("body").inner_text(timeout=5_000)).strip()
        screenshots = await _save_at_most_two_screenshots(page, output_dir)
        return ProfileSnapshot(
            profile_url=profile_url,
            visible_text=text[:12_000],
            ip_location=profile_ip_location,
            screenshot_paths=screenshots,
        )
    except Exception as exc:
        return ProfileSnapshot(profile_url=profile_url, status="异常", error=f"主页访问失败：{type(exc).__name__}")


async def capture_profile_with_browser(profile_url: str, output_dir: Path, cdp_endpoint: str = "") -> ProfileSnapshot:
    launcher: BrowserLauncher | None = None
    try:
        async with async_playwright() as playwright:
            endpoint = cdp_endpoint or f"http://127.0.0.1:{getattr(config, 'CDP_DEBUG_PORT', 9222)}"
            try:
                browser = await playwright.chromium.connect_over_cdp(endpoint)
            except Exception:
                launcher = BrowserLauncher()
                paths = launcher.detect_browser_paths()
                if not paths:
                    return ProfileSnapshot(profile_url=profile_url, status="异常", error="未找到可用 Chrome 或 Edge 浏览器")
                port = launcher.find_available_port(getattr(config, "CDP_DEBUG_PORT", 9222))
                profile_dir = shared_xhs_login_profile_dir()
                launcher.launch_browser(paths[0], port, headless=False, user_data_dir=str(profile_dir))
                ready = await asyncio.to_thread(launcher.wait_for_browser_ready, port, 30)
                if not ready:
                    return ProfileSnapshot(profile_url=profile_url, status="异常", error="浏览器启动超时")
                browser = await playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            return await capture_visible_profile(page, profile_url, output_dir)
    except Exception as exc:
        return ProfileSnapshot(profile_url=profile_url, status="异常", error=f"浏览器连接失败：{type(exc).__name__}")


def _snapshot_to_dict(snapshot: ProfileSnapshot) -> dict[str, Any]:
    return {
        "profile_url": snapshot.profile_url,
        "visible_text": snapshot.visible_text,
        "ip_location": snapshot.ip_location,
        "screenshot_paths": snapshot.screenshot_paths,
        "status": snapshot.status,
        "error": snapshot.error,
    }


def main() -> None:
    configure_utf8_stdout()
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--cdp", default="")
    args = parser.parse_args()
    snapshot = asyncio.run(capture_profile_with_browser(args.profile_url, Path(args.output_dir), args.cdp))
    print(json.dumps(_snapshot_to_dict(snapshot), ensure_ascii=False))


if __name__ == "__main__":
    main()
