# -*- coding: utf-8 -*-
"""
Graytun (灰豚数据) browser automation helpers.

This script deliberately uses the product UI instead of private API endpoints.
It is intended for exporting member-available tables, then importing the
downloaded Excel/CSV into the local MediaCrawler workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError, sync_playwright


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_DATA_DIR = PROJECT_ROOT / "browser_data" / "huitun_user_data_dir"
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads" / "huitun"
DEFAULT_SCREENSHOT = PROJECT_ROOT / "downloads" / "huitun" / "huitun_page.png"
HUITUN_HOME = "https://xhs.huitun.com/#/"
HUITUN_ANCHOR_LIST = "https://xhs.huitun.com/#/anchor/anchor_list"


@dataclass
class BrowserSession:
    page: Page
    context: BrowserContext
    browser: Optional[Browser] = None
    connected_over_cdp: bool = False


def _log(message: str) -> None:
    print(message, flush=True)


def _json_line(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _first_visible_page(context: BrowserContext) -> Page:
    for page in context.pages:
        if not page.is_closed():
            return page
    return context.new_page()


def open_browser_session(playwright, args: argparse.Namespace) -> BrowserSession:
    if args.cdp:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0] if browser.contexts else browser.new_context(accept_downloads=True)
        page = _first_visible_page(context)
        return BrowserSession(page=page, context=context, browser=browser, connected_over_cdp=True)

    user_data_dir = Path(args.user_data_dir).resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)
    launch_options = {
        "headless": args.headless,
        "accept_downloads": True,
        "viewport": {"width": args.width, "height": args.height},
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
    }
    if args.executable_path:
        launch_options["executable_path"] = args.executable_path
    elif args.channel:
        launch_options["channel"] = args.channel

    context = playwright.chromium.launch_persistent_context(str(user_data_dir), **launch_options)
    page = _first_visible_page(context)
    return BrowserSession(page=page, context=context)


def close_browser_session(session: BrowserSession, keep_open: bool) -> None:
    if keep_open:
        return
    if session.connected_over_cdp:
        if session.browser:
            session.browser.close()
        return
    session.context.close()


def goto_and_wait(page: Page, url: str, wait_ms: int = 2500) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except TimeoutError:
        pass
    page.wait_for_timeout(wait_ms)


def visible_text(page: Page, limit: int = 5000) -> str:
    try:
        text = page.locator("body").inner_text(timeout=10_000)
    except TimeoutError:
        return ""
    return text[:limit]


def login_state(page: Page) -> str:
    text = visible_text(page, limit=8000)
    if "扫码登录" in text or "登录/注册" in text or "欢迎使用灰豚数据" in text:
        return "login_required"
    if "企业版" in text or "工作台" in text or "达人查找" in text:
        return "logged_in_or_public"
    return "unknown"


def click_text(page: Page, text: str, exact: bool = True, timeout: int = 8000) -> bool:
    candidates = [
        page.get_by_role("button", name=text, exact=exact),
        page.get_by_text(text, exact=exact),
    ]
    for locator in candidates:
        try:
            locator.first.click(timeout=timeout)
            return True
        except Exception:
            continue
    box = find_visible_text_box(page, text, exact=exact)
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return True
    return False


def find_visible_text_box(page: Page, text: str, exact: bool = True) -> Optional[dict]:
    return page.evaluate(
        """([target, exact]) => {
            const norm = (value) => String(value || '').replace(/\\s/g, '').trim();
            const wanted = norm(target);
            const nodes = Array.from(document.querySelectorAll('button, [role="button"], .ant-tabs-tab, .ant-btn, a, div, span, li'));
            const candidates = nodes
                .map((el) => {
                    const raw = String(el.innerText || el.textContent || '');
                    const text = norm(raw);
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return {
                        raw,
                        text,
                        tag: el.tagName,
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                    };
                })
                .filter((item) => {
                    if (!item.visible || !item.text) return false;
                    return exact ? item.text === wanted : item.text.includes(wanted);
                });
            if (!candidates.length) return null;
            candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
            return candidates[0];
        }""",
        [text, exact],
    )


def select_rank_tab(page: Page, rank_tab: str) -> None:
    if not rank_tab:
        return
    if not click_text(page, rank_tab, exact=True, timeout=5000):
        raise RuntimeError(f"没有找到榜单标签：{rank_tab}")
    page.wait_for_timeout(1200)


def select_category(page: Page, category: str) -> None:
    if not category:
        return
    if not click_text(page, category, exact=True, timeout=5000):
        raise RuntimeError(f"没有找到达人分类：{category}")
    page.wait_for_timeout(1200)


def find_export_button_box(page: Page) -> Optional[dict]:
    return page.evaluate(
        """() => {
            const nodes = Array.from(document.querySelectorAll('button, [role="button"], .ant-btn, a, div, span'));
            const candidates = nodes
                .map((el) => {
                    const text = String(el.innerText || el.textContent || '').replace(/\\s/g, '');
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return {
                        text,
                        tag: el.tagName,
                        x: rect.x,
                        y: rect.y,
                        width: rect.width,
                        height: rect.height,
                        visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                    };
                })
                .filter((item) => item.visible && item.text === '导出');
            if (!candidates.length) return null;
            candidates.sort((a, b) => (a.width * a.height) - (b.width * b.height));
            return candidates[0];
        }"""
    )


def wait_for_download_after_export(page: Page, download_dir: Path, timeout_ms: int) -> Path:
    download_dir.mkdir(parents=True, exist_ok=True)
    box = None
    for _ in range(20):
        box = find_export_button_box(page)
        if box:
            break
        page.wait_for_timeout(500)
    if not box:
        raise RuntimeError("没有找到“导出”按钮，可能页面未进入达人榜单或当前账号无导出入口")

    try:
        with page.expect_download(timeout=timeout_ms) as download_info:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        download = download_info.value
    except TimeoutError:
        confirm_labels = ["确认导出", "确定导出", "立即导出", "继续导出", "确认", "确定", "下载"]
        for label in confirm_labels:
            confirm_box = find_visible_text_box(page, label, exact=True)
            if not confirm_box:
                continue
            try:
                with page.expect_download(timeout=timeout_ms) as download_info:
                    page.mouse.click(
                        confirm_box["x"] + confirm_box["width"] / 2,
                        confirm_box["y"] + confirm_box["height"] / 2,
                    )
                download = download_info.value
                break
            except TimeoutError:
                download = None
                continue
        else:
            download = None

        if download is None:
            debug_path = download_dir / "after_export_click.png"
            page.screenshot(path=str(debug_path), full_page=False)
            page_text = visible_text(page, limit=3000)
            raise RuntimeError(
                "点击“导出”后没有捕获到下载文件，可能需要会员权限、二次确认、下载中心异步生成，或页面弹出了提示。"
                f"已保存点击后截图: {debug_path}。页面文本摘要: {page_text[-1000:]}"
            )

    suggested = download.suggested_filename or "huitun_export.xlsx"
    target = download_dir / suggested
    suffix = 1
    while target.exists():
        target = download_dir / f"{target.stem}_{suffix}{target.suffix}"
        suffix += 1
    download.save_as(str(target))
    return target


def action_login(args: argparse.Namespace) -> None:
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, HUITUN_HOME)
        _log("已打开灰豚页面。请在弹出的浏览器里完成登录。")
        _log("登录完成后回到这个终端按 Enter；登录态会保存到 user-data-dir。")
        try:
            input()
        finally:
            _json_line({"status": login_state(page), "url": page.url, "user_data_dir": str(Path(args.user_data_dir).resolve())})
            close_browser_session(session, keep_open=args.keep_open)


def action_login_wait(args: argparse.Namespace) -> None:
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, HUITUN_HOME)
        deadline = page.evaluate("Date.now()") + args.login_timeout_ms
        last_state = login_state(page)
        while page.evaluate("Date.now()") < deadline:
            last_state = login_state(page)
            if last_state != "login_required":
                break
            page.wait_for_timeout(1500)
        _json_line(
            {
                "status": last_state,
                "url": page.url,
                "user_data_dir": str(Path(args.user_data_dir).resolve()),
                "login_timeout_ms": args.login_timeout_ms,
            }
        )
        close_browser_session(session, keep_open=args.keep_open)


def action_screenshot(args: argparse.Namespace) -> None:
    screenshot_path = Path(args.output or DEFAULT_SCREENSHOT).resolve()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, args.url or HUITUN_HOME)
        page.screenshot(path=str(screenshot_path), full_page=args.full_page)
        _json_line(
            {
                "status": login_state(page),
                "url": page.url,
                "screenshot": str(screenshot_path),
                "text_preview": visible_text(page, limit=1200),
            }
        )
        close_browser_session(session, keep_open=args.keep_open)


def action_export_anchor_list(args: argparse.Namespace) -> None:
    download_dir = Path(args.download_dir).resolve()
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, HUITUN_ANCHOR_LIST)
        state = login_state(page)
        if state == "login_required":
            raise RuntimeError(
                "当前灰豚会话未登录。先运行 login 动作完成登录，或用 --cdp 连接已登录且开启远程调试的 Chrome。"
            )

        select_rank_tab(page, args.rank_tab)
        select_category(page, args.category)

        if args.screenshot_before_export:
            shot = download_dir / "anchor_list_before_export.png"
            download_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(shot), full_page=False)

        target = wait_for_download_after_export(page, download_dir, args.download_timeout_ms)
        _json_line(
            {
                "status": "ok",
                "action": "export_anchor_list",
                "rank_tab": args.rank_tab,
                "category": args.category,
                "download": str(target),
                "url": page.url,
            }
        )
        close_browser_session(session, keep_open=args.keep_open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="灰豚数据 UI 自动化")
    parser.add_argument("action", choices=["login", "login-wait", "screenshot", "export-anchor-list"])
    parser.add_argument("--user-data-dir", default=str(DEFAULT_USER_DATA_DIR), help="Playwright 持久化浏览器目录")
    parser.add_argument("--cdp", default="", help="连接已开启远程调试的 Chrome，例如 http://127.0.0.1:9222")
    parser.add_argument("--channel", default="chrome", help="Playwright 浏览器 channel；留空则用 bundled chromium")
    parser.add_argument("--executable-path", default="", help="指定 Chrome/Edge 可执行文件路径")
    parser.add_argument("--headless", action="store_true", help="无头模式；首次登录不要使用")
    parser.add_argument("--keep-open", action="store_true", help="动作结束后不主动关闭浏览器")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)

    parser.add_argument("--url", default="", help="screenshot 动作使用的 URL")
    parser.add_argument("--output", default="", help="screenshot 动作输出路径")
    parser.add_argument("--full-page", action="store_true")

    parser.add_argument("--download-dir", default=str(DEFAULT_DOWNLOAD_DIR), help="导出文件保存目录")
    parser.add_argument("--rank-tab", default="涨粉榜", help="榜单标签：涨粉榜/商业推广榜/地域榜/爆文榜")
    parser.add_argument("--category", default="", help="达人分类，例如 美妆、摄影、家居家装")
    parser.add_argument("--download-timeout-ms", type=int, default=60_000)
    parser.add_argument("--login-timeout-ms", type=int, default=300_000)
    parser.add_argument("--screenshot-before-export", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.channel.strip().lower() in {"", "none", "bundled"}:
        args.channel = ""
    try:
        if args.action == "login":
            action_login(args)
        elif args.action == "login-wait":
            action_login_wait(args)
        elif args.action == "screenshot":
            action_screenshot(args)
        elif args.action == "export-anchor-list":
            action_export_anchor_list(args)
        else:
            parser.error(f"unsupported action: {args.action}")
    except Exception as exc:
        _json_line({"status": "error", "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
