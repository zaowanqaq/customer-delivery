# -*- coding: utf-8 -*-
"""Collect only the currently visible information from a Xiaohongshu profile page."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
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
SCREENING_PAGE_NAME = "mediacrawler_creator_screening"
DEFAULT_PROFILE_NAVIGATION_TIMEOUT_MS = 60_000
DEFAULT_PROFILE_TEXT_TIMEOUT_MS = 15_000


def _bounded_timeout_ms(env_name: str, default: int) -> int:
    try:
        configured = int(os.getenv(env_name, str(default)))
    except ValueError:
        configured = default
    return max(5_000, min(configured, 180_000))


def shared_xhs_login_profile_dir() -> Path:
    return browser_data_dir() / f"cdp_{config.USER_DATA_DIR % config.PLATFORM}"


def configure_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


async def _dismiss_login_prompt(page: Page) -> None:
    """Close the non-blocking XHS login overlay when profile data is still visible.

    Some XHS web variants do not bind Escape to the login prompt.  Prefer the
    visible close control inside the overlay, then retain Escape as a fallback.
    """
    content = await page.content()
    if not any(marker in content for marker in LOGIN_MARKERS):
        return

    close_script = """
    () => {
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden'
          && Number(style.opacity || 1) > 0 && rect.width > 0 && rect.height > 0;
      };
      const overlays = Array.from(document.querySelectorAll(
        '[role="dialog"], [class*="modal" i], [class*="login" i]'
      )).filter(visible);
      for (const overlay of overlays) {
        const close = overlay.querySelector(
          '[aria-label*="关闭"], [title*="关闭"], [class*="close" i]'
        );
        if (!close || !visible(close)) continue;
        (close.closest('button, [role="button"]') || close).click();
        return true;
      }
      return false;
    }
    """
    try:
        closed = bool(await page.evaluate(close_script))
    except Exception:
        closed = False
    if not closed:
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)


async def _get_or_create_screening_page(context, page_name: str = SCREENING_PAGE_NAME) -> Page:
    """Reuse a worker's marked tab so screening does not accumulate XHS tabs."""
    for existing_page in context.pages:
        try:
            if await existing_page.evaluate("window.name") == page_name:
                return existing_page
        except Exception:
            continue
    page = await context.new_page()
    await page.evaluate(f"window.name = {json.dumps(page_name)}")
    return page


def extract_profile_ip(text: str) -> str:
    match = re.search(r"(?:IP属地|IP 地|IP所在地)\s*[：:]?\s*([^\s|｜，,。]{1,20})", text)
    return match.group(1).strip() if match else ""


def _is_login_only_page(text: str, page_content: str = "") -> bool:
    source = f"{text}\n{page_content}"
    if "小红书号" in source or "IP属地" in source:
        return False
    return any(marker in source for marker in ("手机号登录", "扫码", "获取验证码", "登录后推荐"))


async def _save_profile_screenshot(page: Page, output_dir: Path) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "profile.png"
    await page.screenshot(path=str(path), full_page=False)
    return [str(path)]


async def capture_visible_profile(page: Page, profile_url: str, output_dir: Path) -> ProfileSnapshot:
    try:
        await page.goto(
            profile_url,
            wait_until="domcontentloaded",
            timeout=_bounded_timeout_ms("CREATOR_SCREENING_PROFILE_TIMEOUT_MS", DEFAULT_PROFILE_NAVIGATION_TIMEOUT_MS),
        )
        await _dismiss_login_prompt(page)
        profile_header_text = (
            await page.locator("body").inner_text(
                timeout=_bounded_timeout_ms("CREATOR_SCREENING_TEXT_TIMEOUT_MS", DEFAULT_PROFILE_TEXT_TIMEOUT_MS)
            )
        ).strip()
        profile_ip_location = extract_profile_ip(profile_header_text)
        for _ in range(2):
            await page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight * 0.8))")
            await page.wait_for_timeout(350)
        text = (await page.locator("body").inner_text(timeout=5_000)).strip()
        screenshots = await _save_profile_screenshot(page, output_dir)
        if _is_login_only_page(text, await page.content()):
            return ProfileSnapshot(
                profile_url=profile_url,
                visible_text=text[:12_000],
                screenshot_paths=screenshots,
                status="待人工确认",
                error="主页只显示登录页，未找到可见资料",
            )
        return ProfileSnapshot(
            profile_url=profile_url,
            visible_text=text[:12_000],
            ip_location=profile_ip_location,
            screenshot_paths=screenshots,
        )
    except Exception as exc:
        return ProfileSnapshot(profile_url=profile_url, status="异常", error=f"主页访问失败：{type(exc).__name__}")


async def capture_profile_with_browser(
    profile_url: str,
    output_dir: Path,
    cdp_endpoint: str = "",
    screening_page_name: str = SCREENING_PAGE_NAME,
) -> ProfileSnapshot:
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
            page = await _get_or_create_screening_page(context, screening_page_name)
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
    parser.add_argument("--screening-page-name", default=SCREENING_PAGE_NAME)
    args = parser.parse_args()
    snapshot = asyncio.run(
        capture_profile_with_browser(args.profile_url, Path(args.output_dir), args.cdp, args.screening_page_name)
    )
    print(json.dumps(_snapshot_to_dict(snapshot), ensure_ascii=False))


if __name__ == "__main__":
    main()
