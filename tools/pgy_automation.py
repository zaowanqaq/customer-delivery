# -*- coding: utf-8 -*-
"""
Xiaohongshu Pugongying (蒲公英) browser automation helpers.

The first supported workflow opens the pre-trade KOL note page and selects the
"搜昵称" tab. It uses the visible UI rather than private API endpoints.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from playwright.sync_api import Browser, BrowserContext, Page, TimeoutError, sync_playwright


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_DATA_DIR = PROJECT_ROOT / "browser_data" / "pgy_user_data_dir"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "downloads" / "pgy"
PGY_KOL_NOTE_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"


@dataclass
class BrowserSession:
    page: Page
    context: BrowserContext
    browser: Optional[Browser] = None
    connected_over_cdp: bool = False


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
        return BrowserSession(
            page=_first_visible_page(context),
            context=context,
            browser=browser,
            connected_over_cdp=True,
        )

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
    return BrowserSession(page=_first_visible_page(context), context=context)


def close_browser_session(session: BrowserSession, keep_open: bool) -> None:
    if keep_open:
        try:
            input("浏览器保持打开中。按 Enter 结束脚本...")
        except EOFError:
            pass
    if session.connected_over_cdp:
        if session.browser:
            session.browser.close()
        return
    session.context.close()


def goto_and_wait(page: Page, url: str, wait_ms: int = 3000) -> None:
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
    login_terms = ["扫码登录", "登录后使用", "登录小红书", "手机号登录", "验证码登录"]
    if any(term in text for term in login_terms):
        return "login_required"
    if "搜昵称" in text or "达人" in text or "蒲公英" in text or "合作" in text:
        return "logged_in_or_public"
    return "unknown"


def find_visible_text_box(page: Page, text: str, exact: bool = True) -> Optional[dict]:
    return page.evaluate(
        """([target, exact]) => {
            const norm = (value) => String(value || '').replace(/\\s/g, '').trim();
            const wanted = norm(target);
            const nodes = Array.from(document.querySelectorAll('button, [role="button"], .ant-tabs-tab, .reds-tabs-tab, .d-text, a, div, span, li'));
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


def click_text_in_content(page: Page, text: str, exact: bool = True, timeout: int = 3000) -> bool:
    """Click matching text in the page body, away from the top navigation menu."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        box = page.evaluate(
            """([text, exact]) => {
                const wanted = String(text || '').replace(/\\s+/g, '');
                const norm = (value) => String(value || '').replace(/\\s+/g, '');
                const candidates = Array.from(document.querySelectorAll('button, span, div, a, li'))
                    .map((el) => {
                        const raw = String(el.innerText || el.textContent || '');
                        const label = norm(raw);
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return {
                            label,
                            x: rect.x,
                            y: rect.y,
                            width: rect.width,
                            height: rect.height,
                            visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                        };
                    })
                    .filter((item) => {
                        if (!item.visible || !item.label) return false;
                        if (item.y < 84) return false;
                        if (item.width > 360 || item.height > 80) return false;
                        return exact ? item.label === wanted : item.label.includes(wanted);
                    });
                if (!candidates.length) return null;
                candidates.sort((a, b) => (a.y - b.y) || ((a.width * a.height) - (b.width * b.height)));
                return candidates[0];
            }""",
            [text, exact],
        )
        if box:
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
        page.wait_for_timeout(250)
    return False


def dismiss_overlays(page: Page) -> None:
    labels = ["跳过", "知道了", "我知道了", "暂不", "关闭"]
    for _ in range(4):
        dismissed = False
        for label in labels:
            if click_text(page, label, exact=True, timeout=1200):
                page.wait_for_timeout(800)
                dismissed = True
                break
        if not dismissed:
            break


def ensure_find_creator_page(page: Page) -> None:
    if "/solar/pre-trade/note/kol" not in page.url:
        page.goto(PGY_KOL_NOTE_URL, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(1500)
    dismiss_overlays(page)
    page.keyboard.press("Escape")
    page.mouse.click(700, 160)
    page.wait_for_timeout(500)


def select_search_nickname_tab(page: Page) -> bool:
    dismiss_overlays(page)
    page.keyboard.press("Escape")
    page.mouse.wheel(0, -1200)
    page.wait_for_timeout(500)

    for _ in range(3):
        if click_text_in_content(page, "搜昵称", exact=True, timeout=2500) or click_text_in_content(page, "搜昵称", exact=False, timeout=1500):
            page.wait_for_timeout(1200)
            inputs = visible_inputs(page)
            if any(any(term in item["placeholder"] for term in ["昵称", "小红书号", "博主"]) for item in inputs):
                return True
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    return False


def save_screenshot(page: Page, name: str) -> str:
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = DEFAULT_OUTPUT_DIR / name
    page.screenshot(path=str(path), full_page=False)
    return str(path)


def visible_inputs(page: Page) -> list[dict]:
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('input, textarea'))
            .map((el, index) => {
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return {
                    index,
                    placeholder: el.getAttribute('placeholder') || '',
                    value: el.value || '',
                    type: el.getAttribute('type') || '',
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none'
                };
            })
            .filter((item) => item.visible)
            .sort((a, b) => (a.y - b.y) || (a.x - b.x))"""
    )


def fill_nickname_keyword(page: Page, nickname: str) -> dict:
    inputs = visible_inputs(page)
    if not inputs:
        raise RuntimeError("没有找到可输入的搜索框")
    nickname_terms = ["昵称", "小红书号", "博主"]
    candidates = [item for item in inputs if any(term in item["placeholder"] for term in nickname_terms)]
    if not candidates:
        save_screenshot(page, "pgy_nickname_input_not_found.png")
        raise RuntimeError("没有找到昵称搜索框，当前页面可能仍停留在“搜笔记”")
    candidates = sorted(candidates, key=lambda item: (item["y"], -item["width"]))
    target = candidates[0]
    page.mouse.click(target["x"] + min(24, target["width"] / 2), target["y"] + target["height"] / 2)
    page.keyboard.press("Control+A")
    page.keyboard.type(nickname, delay=20)
    page.wait_for_timeout(400)
    if not click_text(page, "搜索", exact=True, timeout=2500):
        page.keyboard.press("Enter")
    page.wait_for_timeout(3500)
    return target


def click_first_kol_result(page: Page, nickname: str) -> bool:
    before_pages = len(page.context.pages)
    if click_text(page, nickname, exact=True, timeout=4000) or click_text(page, nickname, exact=False, timeout=4000):
        page.wait_for_timeout(2500)
    else:
        # Fallback: click the first visible avatar/name area in the result table.
        box = page.evaluate(
            """() => {
                const rows = Array.from(document.querySelectorAll('tr, [class*=table] [class*=row], [class*=card]'));
                for (const row of rows) {
                    const text = String(row.innerText || row.textContent || '');
                    if (!text || text.includes('博主信息') || text.includes('操作')) continue;
                    const rect = row.getBoundingClientRect();
                    const style = getComputedStyle(row);
                    if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden') {
                        return {x: rect.x + Math.min(90, rect.width / 5), y: rect.y + Math.min(44, rect.height / 2), width: rect.width, height: rect.height, text};
                    }
                }
                return null;
            }"""
        )
        if not box:
            return False
        page.mouse.click(box["x"], box["y"])
        page.wait_for_timeout(2500)

    if len(page.context.pages) > before_pages:
        new_page = page.context.pages[-1]
        new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        page = new_page
    return True


def active_page(context: BrowserContext, fallback: Page) -> Page:
    pages = [page for page in context.pages if not page.is_closed()]
    return pages[-1] if pages else fallback


def scrape_section_from_text(text: str, start_terms: list[str], end_terms: list[str]) -> str:
    start = -1
    for term in start_terms:
        idx = text.find(term)
        if idx >= 0 and (start < 0 or idx < start):
            start = idx
    if start < 0:
        return ""
    end = len(text)
    for term in end_terms:
        idx = text.find(term, start + 1)
        if idx > start and idx < end:
            end = idx
    return text[start:end].strip()


def extract_similar_creators(text: str, limit: int = 20) -> list[dict]:
    section = scrape_section_from_text(
        text,
        ["相似博主", "相似的博主推荐", "相似博主推荐"],
        ["更多", "粉丝分析", "传播表现", "合作案例"],
    )
    if not section:
        section = text
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    rows: list[dict] = []
    skip_words = {"相似博主", "相似的博主推荐", "查看更多", "添加合作", "发起邀约", "更多操作"}
    for i, line in enumerate(lines):
        if line in skip_words or len(line) > 40:
            continue
        if any(token in line for token in ["¥", "粉丝", "阅读", "互动", "合作", "报价", "起"]):
            continue
        next_text = " | ".join(lines[i + 1 : i + 8])
        rows.append({"rank": len(rows) + 1, "nickname": line, "nearby_text": next_text})
        if len(rows) >= limit:
            break
    return rows


def write_outputs(nickname: str, detail_text: str, page: Page) -> dict:
    safe_name = "".join(ch for ch in nickname if ch not in r'\/:*?"<>|').strip() or "kol"
    output_dir = DEFAULT_OUTPUT_DIR / safe_name
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshot = output_dir / "detail.png"
    page.screenshot(path=str(screenshot), full_page=True)

    detail_path = output_dir / "detail_text.txt"
    detail_path.write_text(detail_text, encoding="utf-8")

    propagation = scrape_section_from_text(
        detail_text,
        ["传播表现", "数据表现", "日常笔记"],
        ["粉丝分析", "粉丝画像", "相似博主", "合作报价"],
    )
    fans = scrape_section_from_text(
        detail_text,
        ["粉丝分析", "粉丝画像"],
        ["传播表现", "相似博主", "合作报价", "合作案例"],
    )
    similar = extract_similar_creators(detail_text, limit=20)

    summary_path = output_dir / "summary.json"
    summary = {
        "nickname": nickname,
        "url": page.url,
        "screenshot": str(screenshot),
        "detail_text": str(detail_path),
        "propagation_performance": propagation,
        "fan_analysis": fans,
        "similar_creators": similar,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    similar_path = output_dir / "similar_creators.csv"
    with similar_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "nickname", "nearby_text"])
        writer.writeheader()
        writer.writerows(similar)

    return {
        "output_dir": str(output_dir),
        "summary": str(summary_path),
        "similar_csv": str(similar_path),
        "screenshot": str(screenshot),
        "detail_text": str(detail_path),
        "similar_count": len(similar),
    }


def find_kol_from_api_records(records: list[dict], nickname: str, red_id: str = "") -> dict:
    candidates: list[dict] = []
    for record in records:
        data = record.get("data") or {}
        if isinstance(data, dict):
            payload = data.get("data") or data
            if isinstance(payload, dict):
                candidates.extend(payload.get("kols") or [])
    if red_id:
        red_id = red_id.strip()
        red_exact = [item for item in candidates if str(item.get("redId") or "") == red_id]
        if red_exact:
            return red_exact[0]
    exact = [item for item in candidates if item.get("name") == nickname]
    fuzzy = [item for item in candidates if nickname in str(item.get("name") or "")]
    if exact:
        return exact[0]
    if fuzzy:
        return fuzzy[0]
    if candidates:
        return candidates[0]
    return {}


def latest_similar_kols_from_records(records: list[dict], original_user_id: str) -> list[dict]:
    best: list[dict] = []
    for record in records:
        data = record.get("data") or {}
        payload = data.get("data") if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            continue
        kols = payload.get("kols") or []
        total = payload.get("total") or 0
        filtered = [kol for kol in kols if kol.get("userId") != original_user_id]
        if len(filtered) > len(best) or total > 20 and len(filtered) >= len(best):
            best = filtered
    return best[:20]


def click_similar_more(page: Page, nickname: str) -> bool:
    box = page.evaluate(
        """(nickname) => {
            const exactTitle = `与 ${nickname} 相似的博主推荐`;
            const blocks = Array.from(document.querySelectorAll('*'))
                .filter((el) => String(el.innerText || '').includes(exactTitle) || String(el.innerText || '').includes('相似的博主推荐'));
            let block = blocks.sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height)[0];
            if (!block) return null;
            block.scrollIntoView({block: 'center'});
            const candidates = Array.from(block.querySelectorAll('button, span, div, a'))
                .filter((el) => String(el.innerText || el.textContent || '').trim().includes('查看更多'));
            const target = candidates.sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
            if (!target) return null;
            const rect = target.getBoundingClientRect();
            return {x: rect.x + rect.width / 2, y: rect.y + rect.height / 2};
        }""",
        nickname,
    )
    if not box:
        return False
    page.mouse.click(box["x"], box["y"])
    page.wait_for_timeout(5000)
    return True


def api_get_json(page: Page, path: str, params: Optional[dict] = None) -> dict:
    url = path
    if params:
        url = f"{url}?{urlencode(params)}"
    result = page.evaluate(
        """async (url) => {
            const resp = await fetch(url, {
                method: 'GET',
                credentials: 'include',
                headers: {'accept': 'application/json, text/plain, */*'}
            });
            const text = await resp.text();
            return {ok: resp.ok, status: resp.status, text};
        }""",
        url,
    )
    if not result["ok"]:
        raise RuntimeError(f"GET {path} 失败: HTTP {result['status']}")
    return json.loads(result["text"])


def api_post_json(page: Page, path: str, payload: dict) -> dict:
    result = page.evaluate(
        """async ([url, payload]) => {
            const resp = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: {
                    'accept': 'application/json, text/plain, */*',
                    'content-type': 'application/json;charset=UTF-8'
                },
                body: JSON.stringify(payload)
            });
            const text = await resp.text();
            return {ok: resp.ok, status: resp.status, text};
        }""",
        [path, payload],
    )
    if not result["ok"]:
        raise RuntimeError(f"POST {path} 失败: HTTP {result['status']}")
    return json.loads(result["text"])


def extract_api_payload(raw: dict) -> object:
    if isinstance(raw, dict) and "data" in raw:
        return raw.get("data")
    return raw


def classify_api_response(url: str) -> str:
    if "/api/solar/cooperator/user/blogger/" in url:
        return "blogger_detail"
    if "/api/pgy/kol/data/data_summary" in url:
        return "data_summary"
    if "/api/pgy/kol/data/core_data" in url:
        return "core_data"
    if "/api/solar/kol/data_v3/notes_rate" in url:
        return "notes_rate"
    if "/api/solar/kol/data_v3/fans_summary" in url:
        return "fans_summary"
    if "/fans_profile" in url:
        return "fans_profile"
    if "/fans_overall_new_history" in url:
        return "fans_history"
    if "/api/solar/kol/get_similar_kol" in url:
        return "similar_creators"
    return ""


def collect_detail_api_response(api_data: dict, response) -> None:
    key = classify_api_response(response.url)
    if not key:
        return
    try:
        payload = response.json()
    except Exception:
        return
    if key == "similar_creators":
        existing_count = len(((api_data.get(key) or {}).get("data") or {}).get("kols") or [])
        new_count = len(((payload or {}).get("data") or {}).get("kols") or [])
        if new_count >= existing_count:
            api_data[key] = payload
        return
    api_data[key] = payload


def normalize_kol_row(rank: int, kol: dict) -> dict:
    raw_tags = kol.get("featureTags") or kol.get("contentTags") or []
    tags: list[str] = []
    for tag in raw_tags:
        if isinstance(tag, str):
            tags.append(tag)
        elif isinstance(tag, dict):
            tags.append(str(tag.get("name") or tag.get("tagName") or tag.get("taxonomy1Tag") or tag))
        else:
            tags.append(str(tag))
    return {
        "rank": rank,
        "nickname": kol.get("name") or "",
        "red_id": kol.get("redId") or "",
        "location": kol.get("location") or "",
        "fans_count": kol.get("fansCount") or "",
        "like_collect_count": kol.get("likeCollectCountInfo") or "",
        "business_note_count": kol.get("businessNoteCount") or "",
        "lower_price": kol.get("lowerPrice") or "",
        "picture_price": kol.get("picturePrice") or "",
        "video_price": kol.get("videoPrice") or "",
        "tags": ",".join(tags),
    }


def write_api_outputs(nickname: str, user_id: str, api_data: dict, detail_text: str, page: Page) -> dict:
    safe_name = "".join(ch for ch in nickname if ch not in r'\/:*?"<>|').strip() or "kol"
    output_dir = DEFAULT_OUTPUT_DIR / safe_name
    output_dir.mkdir(parents=True, exist_ok=True)

    screenshot = output_dir / "detail.png"
    page.screenshot(path=str(screenshot), full_page=True)

    detail_path = output_dir / "detail_text.txt"
    detail_path.write_text(detail_text, encoding="utf-8")

    raw_path = output_dir / "raw_api.json"
    raw_path.write_text(json.dumps(api_data, ensure_ascii=False, indent=2), encoding="utf-8")

    similar_kols = ((api_data.get("similar_creators") or {}).get("data") or {}).get("kols") or []
    similar_rows = [normalize_kol_row(index + 1, kol) for index, kol in enumerate(similar_kols[:20])]
    blogger_detail = extract_api_payload(api_data.get("blogger_detail") or {})
    red_id = blogger_detail.get("redId") or ""

    similar_path = output_dir / "similar_creators.csv"
    with similar_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "rank",
            "nickname",
            "red_id",
            "location",
            "fans_count",
            "like_collect_count",
            "business_note_count",
            "lower_price",
            "picture_price",
            "video_price",
            "tags",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(similar_rows)

    summary = {
        "nickname": nickname,
        "red_id": red_id,
        "url": page.url,
        "screenshot": str(screenshot),
        "detail_text": str(detail_path),
        "raw_api": str(raw_path),
        "blogger_detail": blogger_detail,
        "propagation_performance": {
            "data_summary": extract_api_payload(api_data.get("data_summary") or {}),
            "core_data": extract_api_payload(api_data.get("core_data") or {}),
            "notes_rate": extract_api_payload(api_data.get("notes_rate") or {}),
        },
        "fan_analysis": {
            "fans_summary": extract_api_payload(api_data.get("fans_summary") or {}),
            "fans_profile": extract_api_payload(api_data.get("fans_profile") or {}),
            "fans_history": extract_api_payload(api_data.get("fans_history") or {}),
        },
        "similar_creators": similar_rows,
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "output_dir": str(output_dir),
        "summary": str(summary_path),
        "raw_api": str(raw_path),
        "similar_csv": str(similar_path),
        "screenshot": str(screenshot),
        "detail_text": str(detail_path),
        "similar_count": len(similar_rows),
        "red_id": red_id,
    }


def action_login(args: argparse.Namespace) -> None:
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, PGY_KOL_NOTE_URL)
        print("已打开蒲公英页面。请在浏览器里完成登录，然后回到终端按 Enter。", flush=True)
        try:
            input()
        finally:
            _json_line(
                {
                    "status": login_state(page),
                    "url": page.url,
                    "user_data_dir": str(Path(args.user_data_dir).resolve()),
                    "screenshot": save_screenshot(page, "login_state.png"),
                }
            )
            close_browser_session(session, keep_open=args.keep_open)


def action_screenshot(args: argparse.Namespace) -> None:
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, args.url or PGY_KOL_NOTE_URL)
        _json_line(
            {
                "status": login_state(page),
                "url": page.url,
                "screenshot": save_screenshot(page, "pgy_page.png"),
                "text_preview": visible_text(page, limit=1600),
            }
        )
        close_browser_session(session, keep_open=args.keep_open)


def action_click_nickname(args: argparse.Namespace) -> None:
    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        goto_and_wait(page, PGY_KOL_NOTE_URL)
        state = login_state(page)
        clicked = False
        if state != "login_required":
            dismiss_overlays(page)
            ensure_find_creator_page(page)
            clicked = select_search_nickname_tab(page)
            page.wait_for_timeout(1500)
        _json_line(
            {
                "status": state,
                "clicked": clicked,
                "url": page.url,
                "screenshot": save_screenshot(page, "after_click_nickname.png"),
                "text_preview": visible_text(page, limit=2400),
            }
        )
        if state == "login_required":
            close_browser_session(session, keep_open=True)
        else:
            close_browser_session(session, keep_open=args.keep_open)


def action_run_kol(args: argparse.Namespace) -> None:
    if not args.nickname and not args.red_id:
        args.nickname = input("请输入达人昵称：").strip()
        args.red_id = input("请输入小红书号（可留空）：").strip()
    elif not args.nickname:
        args.nickname = args.red_id

    with sync_playwright() as playwright:
        session = open_browser_session(playwright, args)
        page = session.page
        list_api_records: list[dict] = []
        detail_api_data: dict = {}
        target_red_id = (args.red_id or "").strip()
        search_keyword = target_red_id or args.nickname

        def collect_blogger_list(response) -> None:
            collect_detail_api_response(detail_api_data, response)
            if "/api/solar/cooperator/blogger/v2" not in response.url:
                return
            try:
                list_api_records.append({"url": response.url, "data": response.json()})
            except Exception as exc:
                list_api_records.append({"url": response.url, "error": str(exc)})

        page.on("response", collect_blogger_list)
        goto_and_wait(page, PGY_KOL_NOTE_URL)
        state = login_state(page)
        result: dict = {"status": state, "nickname": args.nickname, "red_id": target_red_id}
        if state == "login_required":
            result.update(
                {
                    "clicked": False,
                    "url": page.url,
                    "screenshot": save_screenshot(page, "run_kol_login_required.png"),
                    "error": "蒲公英需要登录，请先运行 login 动作",
                }
            )
            _json_line(result)
            close_browser_session(session, keep_open=True)
            return

        dismiss_overlays(page)
        ensure_find_creator_page(page)
        if not select_search_nickname_tab(page):
            raise RuntimeError("没有找到或无法点击“搜昵称”")
        input_meta = fill_nickname_keyword(page, search_keyword)
        page.wait_for_timeout(2500)
        kol = find_kol_from_api_records(list_api_records, args.nickname, target_red_id)
        user_id = kol.get("userId")
        red_id = kol.get("redId") or ""
        matched_name = kol.get("name") or args.nickname
        if not user_id:
            save_screenshot(page, "run_kol_user_id_not_found.png")
            raise RuntimeError("已经完成昵称搜索，但没有从蒲公英列表接口中拿到达人 userId")

        clicked_similar_more = click_similar_more(page, matched_name)
        similar_kols = latest_similar_kols_from_records(list_api_records, user_id) if clicked_similar_more else []
        if similar_kols:
            detail_api_data["similar_creators"] = {
                "code": 0,
                "msg": "成功",
                "success": True,
                "data": {"kols": similar_kols, "source": "cooperator/blogger/v2 similar author list"},
            }

        detail_url = f"https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/{user_id}"
        page.goto(detail_url, wait_until="domcontentloaded", timeout=60_000)
        page = active_page(session.context, page)
        page.wait_for_timeout(6000)

        # Details may need extra tab clicks before all text renders.
        for label in ["传播表现", "粉丝分析"]:
            click_text(page, label, exact=True, timeout=2500)
            page.wait_for_timeout(1200)

        required_keys = {"blogger_detail", "data_summary", "core_data", "notes_rate", "fans_summary", "fans_profile"}
        wait_until = time.time() + 10
        while time.time() < wait_until and not required_keys.issubset(detail_api_data.keys()):
            page.wait_for_timeout(500)

        api_data = detail_api_data
        detail_text = visible_text(page, limit=60000)
        outputs = write_api_outputs(matched_name, user_id, api_data, detail_text, page)
        result.update(
            {
                "clicked": True,
                "nickname": matched_name,
                "red_id": outputs.get("red_id") or red_id,
                "matched_by": "red_id" if target_red_id else "nickname",
                "url": page.url,
                "input": input_meta,
                "outputs": outputs,
                "text_preview": detail_text[:2400],
            }
        )
        _json_line(result)
        close_browser_session(session, keep_open=args.keep_open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="小红书蒲公英 UI 自动化")
    parser.add_argument("action", choices=["login", "screenshot", "click-nickname", "run-kol"])
    parser.add_argument("--user-data-dir", default=str(DEFAULT_USER_DATA_DIR), help="Playwright 持久化浏览器目录")
    parser.add_argument("--cdp", default="", help="连接已开启远程调试的 Chrome，例如 http://127.0.0.1:9222")
    parser.add_argument("--channel", default="chrome", help="Playwright 浏览器 channel；留空则用 bundled chromium")
    parser.add_argument("--executable-path", default="", help="指定 Chrome/Edge 可执行文件路径")
    parser.add_argument("--headless", action="store_true", help="无头模式；首次登录不要使用")
    parser.add_argument("--keep-open", action="store_true", help="动作结束后等待 Enter 再关闭浏览器")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--url", default="", help="screenshot 动作使用的 URL")
    parser.add_argument("--nickname", default="", help="run-kol 动作要搜索的达人昵称")
    parser.add_argument("--red-id", default="", help="小红书号；提供后优先用它搜索并精确匹配达人")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.channel.strip().lower() in {"", "none", "bundled"}:
        args.channel = ""
    try:
        if args.action == "login":
            action_login(args)
        elif args.action == "screenshot":
            action_screenshot(args)
        elif args.action == "click-nickname":
            action_click_nickname(args)
        elif args.action == "run-kol":
            action_run_kol(args)
        else:
            parser.error(f"unsupported action: {args.action}")
    except Exception as exc:
        _json_line({"status": "error", "error": str(exc)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
