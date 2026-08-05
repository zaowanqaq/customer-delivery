# -*- coding: utf-8 -*-
import base64
import contextlib
import hashlib
import io
import json
import math
import asyncio
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import csv
import os
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
from urllib.parse import quote, unquote, urlencode, urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from config.runtime_paths import browser_data_dir, data_dir, downloads_dir, temp_dir
from tools.browser_launcher import BrowserLauncher
from ..schemas import (
    CrawlerStartRequest,
    CrawlerStatusResponse,
    RuleTableStartRequest,
    LocalToBaseSyncRequest,
    SampleCreatorStartRequest,
    SampleAccountImportRequest,
    NoteSentimentStartRequest,
    SentimentRuleSyncRequest,
    ScenarioTableSetupRequest,
    ScenarioBootstrapRequest,
    CollaborationMonitorStartRequest,
    CollaborationMonitorStopRequest,
    HuitunExportAnchorListRequest,
    HuitunLoginRequest,
    HuitunScreenshotRequest,
    PgyKolRunRequest,
    PgyKolSyncRequest,
    PgyLoginRequest,
)
from ..services import crawler_manager
import config

router = APIRouter(prefix="/crawler", tags=["crawler"])
collaboration_monitor_jobs: Dict[str, Dict[str, Any]] = {}
account_monitor_jobs: Dict[str, Dict[str, Any]] = {}
sentiment_monitor_jobs: Dict[str, Dict[str, Any]] = {}
pgy_login_launch_lock = asyncio.Lock()
PGY_CDP_PORT = 9223
PGY_CDP_ENDPOINT = f"http://127.0.0.1:{PGY_CDP_PORT}"
PGY_LOGIN_URL = "https://pgy.xiaohongshu.com/solar/pre-trade/note/kol"
XHS_LOGIN_URL = "https://www.xiaohongshu.com/explore"


def _pgy_cdp_available() -> bool:
    try:
        with urllib.request.urlopen(f"{PGY_CDP_ENDPOINT}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


async def _wait_for_pgy_cdp(timeout_sec: float = 15.0, process: Any = None) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while True:
        if await asyncio.to_thread(_pgy_cdp_available):
            return True
        if process is not None and process.poll() is not None:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.25, remaining))


def _xhs_cdp_available(cdp_endpoint: str) -> bool:
    try:
        with urllib.request.urlopen(f"{cdp_endpoint}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _open_url_in_cdp(cdp_endpoint: str, url: str) -> bool:
    try:
        target = f"{cdp_endpoint}/json/new?{quote(url, safe=':/?&=%')}"
        request = urllib.request.Request(target, method="PUT")
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.status in (200, 201)
    except Exception:
        return False


def _focus_detected_browser() -> bool:
    try:
        launcher = BrowserLauncher()
        browser_paths = launcher.detect_browser_paths()
        focus_browser_window = getattr(launcher, "focus_browser_window", None)
        if not callable(focus_browser_window):
            return False
        return bool(focus_browser_window(browser_paths[0] if browser_paths else None))
    except Exception:
        return False


def _has_cli_option(args: List[str], option: str) -> bool:
    return option in args or any(arg.startswith(f"{option}=") for arg in args)


def _pgy_browser_args(args: List[str]) -> List[str]:
    """Resolve a concrete browser for Pugongying instead of hard-coding Playwright channel=chrome."""
    if any(_has_cli_option(args, option) for option in ("--cdp", "--executable-path", "--channel")):
        return []

    custom_browser = (config.CUSTOM_BROWSER_PATH or "").strip()
    if custom_browser:
        custom_path = Path(custom_browser).expanduser()
        if custom_path.is_file():
            return ["--executable-path", str(custom_path)]

    for browser_path in BrowserLauncher().detect_browser_paths():
        if Path(browser_path).is_file():
            return ["--executable-path", browser_path]

    return ["--channel", ""]


async def _run_huitun_automation(args: List[str], timeout_sec: int = 180) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "tools" / "huitun_automation.py"
    cmd = [sys.executable, str(script_path), *args]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            cwd=str(project_root),
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"灰豚自动化超时（{timeout_sec}s）") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"灰豚自动化启动失败: {exc}") from exc

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    payload: Dict[str, Any] = {}
    for line in reversed(lines):
        try:
            maybe = json.loads(line)
        except Exception:
            continue
        if isinstance(maybe, dict):
            payload = maybe
            break
    if not payload:
        payload = {"status": "error", "error": result.stderr or result.stdout or "灰豚自动化没有返回 JSON"}
    payload["returncode"] = result.returncode
    if result.stderr:
        payload["stderr"] = result.stderr[-1200:]
    if result.returncode != 0 and payload.get("status") != "error":
        payload["status"] = "error"
        payload["error"] = payload.get("error") or result.stderr or result.stdout
    return payload


async def _run_pgy_automation(args: List[str], timeout_sec: int = 240) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / "tools" / "pgy_automation.py"
    final_args = list(args)
    if "--cdp" not in final_args and _pgy_cdp_available():
        final_args.extend(["--cdp", PGY_CDP_ENDPOINT])
    elif "--cdp" not in final_args:
        final_args.extend(_pgy_browser_args(final_args))
    cmd = [sys.executable, str(script_path), *final_args]
    pgy_env = {**os.environ, "PYTHONPATH": str(project_root)}
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            cwd=str(project_root),
            env=pgy_env,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"蒲公英自动化超时（{timeout_sec}s）") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"蒲公英自动化启动失败: {exc}") from exc

    def sanitize_pgy_log(value: Any) -> str:
        text = str(value or "")
        text = re.sub(r"(?i)(cookie:\s*)(.+?)(\n|$)", r"\1[REDACTED]\3", text)
        text = re.sub(r"(?i)(access-token-[^=\\s]+)=([^;\\s]+)", r"\1=[REDACTED]", text)
        text = re.sub(r"(?i)(web_session|customer-sso-sid|solar\\.beaker\\.session\\.id|a1|websectiga|sec_poison_id)=([^;\\s]+)", r"\1=[REDACTED]", text)
        return text

    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    payload: Dict[str, Any] = {}
    progress: List[Dict[str, Any]] = []
    for line in reversed(lines):
        try:
            maybe = json.loads(line)
        except Exception:
            continue
        if isinstance(maybe, dict) and maybe.get("event") == "progress":
            continue
        if isinstance(maybe, dict):
            payload = maybe
            break
    for line in lines:
        try:
            maybe = json.loads(line)
        except Exception:
            continue
        if isinstance(maybe, dict) and maybe.get("event") == "progress":
            maybe["message"] = sanitize_pgy_log(maybe.get("message"))
            progress.append(maybe)
    if not payload:
        payload = {"status": "error", "error": sanitize_pgy_log(result.stderr or result.stdout or "蒲公英自动化没有返回 JSON")}
    if progress:
        payload["progress"] = progress
    payload["returncode"] = result.returncode
    if result.stderr:
        payload["stderr"] = sanitize_pgy_log(result.stderr[-1200:])
    if result.returncode != 0 and payload.get("status") != "error":
        payload["status"] = "error"
        payload["error"] = sanitize_pgy_log(payload.get("error") or result.stderr or result.stdout)
    if payload.get("error"):
        payload["error"] = sanitize_pgy_log(payload.get("error"))
    return payload


def _pgy_login_required(result: Dict[str, Any]) -> bool:
    """Normalize the different login-expired responses emitted by PGY automation."""
    if result.get("status") == "login_required":
        return True
    message = " ".join(
        str(result.get(key) or "")
        for key in ("error", "stderr")
    ).lower()
    return any(term in message for term in ("需要登录", "未登录", "登录态", "login_required", "login 动作"))


def _rule_is_enabled(value) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"是", "true", "1", "yes", "y", "enabled", "on"}


def _split_creator_inputs(raw: str) -> List[str]:
    if not raw:
        return []
    normalized = raw.replace("\n", ",").replace("，", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


SAMPLE_ACCOUNT_HEADER_WORDS = {
    "账号",
    "账号id",
    "小红书号",
    "小红书id",
    "小红书id号",
    "主页",
    "主页链接",
    "链接",
    "达人",
    "达人链接",
    "样本账号",
    "样本账号列表",
    "url",
    "link",
}


def _is_xhs_host(host: str) -> bool:
    host = (host or "").lower().split(":", 1)[0]
    return host in {"xiaohongshu.com", "xhslink.com", "xhs.cn"} or host.endswith(
        (".xiaohongshu.com", ".xhslink.com", ".xhs.cn")
    )


_XHS_URL_PATTERN = re.compile(
    r"https?://(?:[A-Za-z0-9-]+\.)*(?:xiaohongshu\.com|xhslink\.com|xhs\.cn)[^\s<>\"'，。；;！？!）)\]】》,]*",
    flags=re.IGNORECASE,
)
_URL_TRAILING_PUNCTUATION = ".,，。;；:：!?！？、)]}）】》>"


def _extract_xhs_urls(value: str) -> List[str]:
    """Extract usable Xiaohongshu URLs from cells or copied share text."""
    result: List[str] = []
    seen = set()
    for match in _XHS_URL_PATTERN.finditer(str(value or "")):
        url = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not _is_xhs_host(parsed.hostname or ""):
            continue
        key = url.casefold()
        if key not in seen:
            seen.add(key)
            result.append(url)
    return result


class _XhsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow a short-link redirect chain only inside Xiaohongshu domains."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        parsed = urlparse(newurl)
        if parsed.scheme not in {"http", "https"} or not _is_xhs_host(parsed.hostname or ""):
            raise ValueError("短链跳转目标不是小红书主页")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_xhs_url_opener(verify_certificates: bool = True):
    if verify_certificates:
        try:
            import certifi  # type: ignore

            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()
    else:
        # Only used after a certificate-chain failure. Redirects remain locked
        # to Xiaohongshu-owned hosts by _XhsRedirectHandler.
        context = ssl._create_unverified_context()
    return urllib.request.build_opener(
        _XhsRedirectHandler(),
        urllib.request.HTTPSHandler(context=context),
    )


def _is_certificate_verification_error(exc: Exception) -> bool:
    reason = getattr(exc, "reason", None)
    return (
        isinstance(exc, ssl.SSLCertVerificationError)
        or isinstance(reason, ssl.SSLCertVerificationError)
        or "CERTIFICATE_VERIFY_FAILED" in str(exc).upper()
    )


def _resolve_xhs_redirect_url(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"},
    )
    try:
        with _build_xhs_url_opener(verify_certificates=True).open(request, timeout=15) as response:
            return response.geturl()
    except Exception as exc:
        if not _is_certificate_verification_error(exc):
            raise
    with _build_xhs_url_opener(verify_certificates=False).open(request, timeout=15) as response:
        return response.geturl()


def _profile_url_from_link(value: str, resolve_short_link: bool = True) -> str:
    """Return a canonical XHS profile URL from a long or short homepage link."""
    extracted_urls = _extract_xhs_urls(value)
    if not extracted_urls:
        raise ValueError("未识别到小红书账号主页链接")
    text = extracted_urls[0]
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not _is_xhs_host(parsed.hostname or ""):
        raise ValueError("仅支持小红书账号主页长链或短链")

    candidate = text
    profile_match = re.search(r"/user/profile/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not profile_match and resolve_short_link:
        try:
            candidate = _resolve_xhs_redirect_url(text)
        except Exception as exc:
            raise ValueError(f"短链解析失败：{exc}") from exc
        parsed = urlparse(candidate)
        if not _is_xhs_host(parsed.hostname or ""):
            raise ValueError("短链跳转目标不是小红书主页")
        profile_match = re.search(r"/user/profile/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not profile_match:
        raise ValueError("链接未解析到小红书账号主页，请粘贴账号主页链接")
    user_id = unquote(profile_match.group(1)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", user_id):
        raise ValueError("主页链接中的账号标识无效")
    return f"https://www.xiaohongshu.com/user/profile/{user_id}"


def _profile_id_from_url(profile_url: str) -> str:
    """Extract the normalized creator ID from a canonical XHS profile URL."""
    parsed = urlparse(profile_url)
    match = re.search(r"/user/profile/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    return unquote(match.group(1)).strip().lower() if match else ""


def _normalized_note_url_from_link(value: str, resolve_short_link: bool = True) -> str:
    """Return a usable XHS note URL while preserving xsec query parameters."""
    extracted_urls = _extract_xhs_urls(value)
    if not extracted_urls:
        raise ValueError("未识别到小红书笔记链接")
    text = extracted_urls[0]
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not _is_xhs_host(parsed.hostname or ""):
        raise ValueError("仅支持小红书笔记长链或短链")
    candidate = text
    match = re.search(r"/(?:explore|discovery/item)/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not match and resolve_short_link:
        try:
            candidate = _resolve_xhs_redirect_url(text)
        except Exception as exc:
            raise ValueError(f"短链解析失败：{exc}") from exc
        parsed = urlparse(candidate)
        match = re.search(r"/(?:explore|discovery/item)/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not match:
        raise ValueError("链接未解析到小红书笔记，请粘贴笔记链接")
    note_id = unquote(match.group(1)).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,128}", note_id):
        raise ValueError("笔记链接中的笔记标识无效")
    return candidate


def _note_id_from_link(value: str, resolve_short_link: bool = True) -> str:
    """Extract a Xiaohongshu note ID from a long or short note link."""
    candidate = _normalized_note_url_from_link(value, resolve_short_link=resolve_short_link)
    parsed = urlparse(candidate)
    match = re.search(r"/(?:explore|discovery/item)/([^/?#]+)", parsed.path, flags=re.IGNORECASE)
    if not match:
        raise ValueError("链接未解析到小红书笔记，请粘贴笔记链接")
    note_id = unquote(match.group(1)).strip()
    return note_id


def _split_sentiment_keywords(raw: str) -> List[str]:
    keywords: List[str] = []
    seen = set()
    for item in re.split(r"[\n\r,，;；]+", raw or ""):
        value = item.strip()
        if not value:
            continue
        if len(value) > 80:
            raise ValueError(f"关键词过长：{value[:30]}...")
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            keywords.append(value)
    if not keywords:
        raise ValueError("请至少填写一个舆情风险关键词")
    if len(keywords) > 100:
        raise ValueError("舆情风险关键词最多支持 100 个")
    return keywords


def _escape_formula_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_sentiment_risk_groups(raw_groups: List[Any], legacy_keywords: str = "") -> List[Dict[str, Any]]:
    """Validate editable risk categories while retaining compatibility with the old keyword list."""
    groups = raw_groups or []
    if not groups and legacy_keywords:
        groups = [{"name": "电商风险", "keywords": legacy_keywords}]
    result: List[Dict[str, Any]] = []
    seen_names = set()
    for raw_group in groups:
        name = str(getattr(raw_group, "name", None) or (raw_group.get("name") if isinstance(raw_group, dict) else "")).strip()
        keywords_raw = getattr(raw_group, "keywords", None)
        if keywords_raw is None and isinstance(raw_group, dict):
            keywords_raw = raw_group.get("keywords", "")
        if not name:
            raise ValueError("风险类型名称不能为空")
        if len(name) > 40 or any(char in name for char in "\r\n[]"):
            raise ValueError(f"风险类型名称无效：{name[:30]}")
        if name == "舆情风险":
            raise ValueError("风险类型名称不能使用保留列名“舆情风险”")
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError(f"风险类型名称重复：{name}")
        seen_names.add(name_key)
        if isinstance(keywords_raw, list):
            keywords_raw = ",".join(str(keyword) for keyword in keywords_raw)
        result.append({"name": name, "keywords": _split_sentiment_keywords(str(keywords_raw or ""))})
    if not result:
        raise ValueError("请至少保留一个风险类型分组")
    if len(result) > 20:
        raise ValueError("风险类型分组最多支持 20 组")
    return result


def _risk_group_field_name(group_name: str) -> str:
    return f"风险-{group_name}"


def _risk_group_condition(keywords: List[str]) -> str:
    conditions = ", ".join(f'CONTAINTEXT([评论内容], "{_escape_formula_string(keyword)}")' for keyword in keywords)
    return f"OR({conditions})"


def _risk_group_formula(group: Dict[str, Any]) -> str:
    return f'IF({_risk_group_condition(group["keywords"])}, "{_escape_formula_string(group["name"])}", "")'


def _sentiment_keyword_formula(groups: List[Any]) -> str:
    normalized = (
        [{"name": "电商风险", "keywords": groups}]
        if groups and isinstance(groups[0], str)
        else groups
        if groups and isinstance(groups[0], dict) and isinstance(groups[0].get("keywords"), list)
        else _normalize_sentiment_risk_groups(groups)
    )
    keywords = list(dict.fromkeys(keyword for group in normalized for keyword in group["keywords"]))
    matches = [
        f'IF(CONTAINTEXT([评论内容], "{_escape_formula_string(keyword)}"), "{_escape_formula_string(keyword)}、", "")'
        for keyword in keywords
    ]
    return f'REGEXREPLACE(CONCATENATE({", ".join(matches)}), "、$", "")'


def _sentiment_monitor_formula() -> str:
    return 'IF([评论区敏感词] != "", "命中", "未命中")'


def _sentiment_risk_formula(groups: List[Any]) -> str:
    """Return the aggregate risk label; a comment can match more than one category."""
    if groups and isinstance(groups[0], str):
        normalized = [{"name": "电商风险", "keywords": groups}]
    elif groups and isinstance(groups[0], dict) and isinstance(groups[0].get("keywords"), list):
        normalized = groups
    else:
        normalized = _normalize_sentiment_risk_groups(groups)
    if len(normalized) == 1:
        return _risk_group_formula(normalized[0])
    matches = [
        f'IF({_risk_group_condition(group["keywords"])}, "{_escape_formula_string(group["name"])}、", "")'
        for group in normalized
    ]
    return f'REGEXREPLACE(CONCATENATE({", ".join(matches)}), "、$", "")'


def _looks_like_sample_account(value: str) -> bool:
    text = value.strip().strip("\"'“”‘’")
    if not text:
        return False
    if text.lower() in SAMPLE_ACCOUNT_HEADER_WORDS:
        return False
    return bool(_extract_xhs_urls(text))


def _dedupe_sample_accounts(values: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = value.strip().strip("\"'“”‘’")
        if text.lower() in SAMPLE_ACCOUNT_HEADER_WORDS:
            continue
        for url in _extract_xhs_urls(text):
            key = url.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(url)
    return result


def _sample_accounts_from_text(text: str) -> List[str]:
    raw_items = re.split(r"[\n\r,，;；\t]+", text or "")
    return _dedupe_sample_accounts([item.strip() for item in raw_items])


def _sample_accounts_from_excel(content: bytes, suffix: str) -> List[str]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取 Excel 失败：缺少 pandas/openpyxl 依赖。{exc}") from exc

    try:
        if suffix == ".csv":
            frame = pd.read_csv(io.BytesIO(content), header=None, dtype=str, keep_default_na=False)
        else:
            frame = pd.read_excel(io.BytesIO(content), header=None, dtype=str, keep_default_na=False)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}") from exc

    values = [str(item).strip() for item in frame.to_numpy().flatten().tolist()]
    return _dedupe_sample_accounts(values)


def _parse_sample_account_file(filename: str, content: bytes) -> List[str]:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".xlsx", ".xls", ".csv"}:
        return _sample_accounts_from_excel(content, suffix)
    if suffix in {".txt", ".text", ""}:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return _sample_accounts_from_text(content.decode(encoding))
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="文本文件编码无法识别，请使用 UTF-8 或 GB18030")
    raise HTTPException(status_code=400, detail="仅支持 txt、csv、xlsx、xls 文件")


COLLAB_NOTE_REQUIRED_COLUMNS = ("序号", "达人昵称", "小红书id", "发布笔记链接")


def _parse_collaboration_note_file(filename: str, content: bytes) -> List[Dict[str, str]]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="仅支持 csv、xlsx、xls 文件")
    try:
        import pandas as pd  # type: ignore
        if suffix == ".csv":
            frame = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
        else:
            frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}") from exc

    missing = [name for name in COLLAB_NOTE_REQUIRED_COLUMNS if name not in frame.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少必需列：{'、'.join(missing)}")
    rows: List[Dict[str, str]] = []
    seen = set()
    for index, source in enumerate(frame.to_dict(orient="records"), start=2):
        normalized_row = {name: str(source.get(name) or "").strip() for name in COLLAB_NOTE_REQUIRED_COLUMNS}
        if not any(normalized_row.values()):
            continue
        raw_note_link = normalized_row["发布笔记链接"]
        if not raw_note_link:
            raise HTTPException(status_code=400, detail=f"第 {index} 行缺少发布笔记链接")
        extracted_urls = _extract_xhs_urls(raw_note_link)
        if not extracted_urls:
            raise HTTPException(
                status_code=400,
                detail=f"第 {index} 行发布笔记链接格式不正确，请填写小红书笔记长链或短链",
            )
        note_link = extracted_urls[0]
        parsed = urlparse(note_link)
        if parsed.scheme not in {"http", "https"} or not _is_xhs_host(parsed.hostname or ""):
            raise HTTPException(
                status_code=400,
                detail=f"第 {index} 行发布笔记链接格式不正确，请填写小红书笔记长链或短链",
            )
        key = note_link.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized_row["发布笔记链接"] = note_link
        rows.append(normalized_row)
    return rows


def _extract_table_id(payload: Dict[str, Any]) -> str:
    data = payload.get("data", {})
    if isinstance(data, dict):
        for key in ("table_id", "tableId", "id"):
            if isinstance(data.get(key), str) and data.get(key):
                return data[key]
        table = data.get("table")
        if isinstance(table, dict):
            for key in ("table_id", "tableId", "id"):
                if isinstance(table.get(key), str) and table.get(key):
                    return table[key]
    return ""


def _extract_base_name(payload: Dict[str, Any]) -> str:
    data = payload.get("data", {})
    candidates: List[Any] = []
    if isinstance(data, dict):
        candidates.extend([data.get("name"), data.get("title")])
        for key in ("app", "base"):
            nested = data.get(key)
            if isinstance(nested, dict):
                candidates.extend([nested.get("name"), nested.get("title")])
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_base_token(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "/base/" not in text:
        return text
    parsed = urlparse(text)
    path = parsed.path or text
    parts = [p for p in path.split("/") if p]
    for idx, part in enumerate(parts):
        if part == "base" and idx + 1 < len(parts):
            return parts[idx + 1]
    return text.rstrip("/").split("/")[-1].split("?")[0]


def _find_lark_cli() -> str:
    found = shutil.which("lark-cli") or shutil.which("lark-cli.cmd")
    if found:
        return found
    home = Path.home()
    for candidate in (
        home / "nodejs" / "bin" / "lark-cli",
        home / "nodejs" / "bin" / "lark-cli.cmd",
        Path("/opt/homebrew/bin/lark-cli"),
        Path("/usr/local/bin/lark-cli"),
    ):
        if candidate.exists():
            return str(candidate)
    return "lark-cli"


async def _run_lark_cli(cmd: List[str], timeout_sec: int = 30) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]

    def _decode_cli_output(value: bytes) -> str:
        if not value:
            return ""
        candidates = []
        for encoding in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                decoded = value.decode(encoding, errors="replace")
            except Exception:
                continue
            replacement_count = decoded.count("\ufffd")
            control_count = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\r\n\t")
            candidates.append((replacement_count, control_count, decoded))
        if not candidates:
            return value.decode("utf-8", errors="replace")
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates[0][2]

    def _is_rate_limited(text: str) -> bool:
        t = (text or "").lower()
        return "800004135" in t or " limited" in t or "rate limit" in t

    def _is_base_copying(text: str) -> bool:
        t = (text or "").lower()
        return "800004046" in t or "base is copying" in t

    max_retries = 3
    wait_seconds = 2
    last_err = ""
    _lark_env = None
    _node_bin = str(Path.home() / "nodejs" / "bin")
    if Path(_node_bin).is_dir():
        _lark_env = {**os.environ, "PATH": _node_bin + os.pathsep + os.environ.get("PATH", "")}
    for attempt in range(13):
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                timeout=timeout_sec,
                check=False,
                cwd=str(project_root),
                env=_lark_env,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=400, detail="未找到 lark-cli，请先安装并完成授权")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail=f"lark-cli 调用超时（{timeout_sec}s）")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"lark-cli 调用异常: {e}")

        stdout = _decode_cli_output(result.stdout)
        stderr = _decode_cli_output(result.stderr)
        if result.returncode != 0:
            err_msg = (stderr or stdout or f"exit code {result.returncode}")[:1200]
            last_err = err_msg
            if _is_rate_limited(err_msg) and attempt < max_retries:
                await asyncio.sleep(wait_seconds * (attempt + 1))
                continue
            if _is_base_copying(err_msg) and attempt < 12:
                await asyncio.sleep(5)
                continue
            if _is_rate_limited(err_msg):
                raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {err_msg[:400]}")
            if _is_base_copying(err_msg):
                raise HTTPException(status_code=503, detail=f"飞书 Base 正在复制中，请稍后重试。原始信息: {err_msg[:400]}")
            raise HTTPException(status_code=400, detail=f"lark-cli 调用失败: {err_msg[:400]}")

        try:
            payload = json.loads(stdout)
        except Exception as e:
            raw = (stdout or stderr or "")[:400]
            raise HTTPException(status_code=400, detail=f"lark-cli 返回解析失败: {e}；原始输出: {raw}")

        if payload.get("ok"):
            return payload
        payload_text = json.dumps(payload, ensure_ascii=False)
        last_err = payload_text
        if _is_rate_limited(payload_text) and attempt < max_retries:
            await asyncio.sleep(wait_seconds * (attempt + 1))
            continue
        if _is_base_copying(payload_text) and attempt < 12:
            await asyncio.sleep(5)
            continue
        if _is_rate_limited(payload_text):
            raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {payload_text[:400]}")
        if _is_base_copying(payload_text):
            raise HTTPException(status_code=503, detail=f"飞书 Base 正在复制中，请稍后重试。原始信息: {payload_text[:400]}")
        raise HTTPException(status_code=400, detail=f"lark-cli 返回失败: {payload}")
    raise HTTPException(status_code=400, detail=f"lark-cli 调用失败: {last_err[:400]}")


@contextlib.contextmanager
def _lark_json_arg(payload: Dict[str, Any]):
    project_root = Path(__file__).resolve().parents[2]
    tmp_json_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
            dir=str(project_root),
            prefix=".lark_payload_",
        ) as tmp_file:
            json.dump(payload, tmp_file, ensure_ascii=False, allow_nan=False)
            tmp_json_path = tmp_file.name
        yield f"@./{Path(tmp_json_path).name}"
    finally:
        if tmp_json_path:
            with contextlib.suppress(Exception):
                Path(tmp_json_path).unlink(missing_ok=True)


async def _create_table_with_fields(base_token: str, table_name: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+table-create",
            "--as", "user",
            "--base-token", base_token,
            "--name", table_name,
            "--fields", json.dumps(fields, ensure_ascii=False),
        ],
        timeout_sec=30,
    )
    return {"table_name": table_name, "table_id": _extract_table_id(payload), "raw": payload.get("data", {})}


async def _create_base(project_name: str, folder_token: str = "", time_zone: str = "Asia/Shanghai") -> Dict[str, Any]:
    cmd = [
        _find_lark_cli(),
        "base", "+base-create",
        "--as", "user",
        "--name", project_name,
    ]
    if folder_token:
        cmd.extend(["--folder-token", folder_token])
    if time_zone:
        cmd.extend(["--time-zone", time_zone])
    payload = await _run_lark_cli(cmd, timeout_sec=30)
    data = payload.get("data", {})
    base_obj = data.get("base", {}) if isinstance(data, dict) else {}
    token = str(
        (data.get("app_token") if isinstance(data, dict) else "")
        or (data.get("base_token") if isinstance(data, dict) else "")
        or (base_obj.get("base_token") if isinstance(base_obj, dict) else "")
        or ""
    )
    if not token:
        raise HTTPException(status_code=500, detail=f"新建 Base 成功但未返回 token: {data}")
    return {"base_token": token, "raw": data}


async def _copy_base(template_base_token: str, project_name: str, folder_token: str = "", time_zone: str = "Asia/Shanghai") -> Dict[str, Any]:
    source_token = _extract_base_token(template_base_token)
    if not source_token:
        raise HTTPException(status_code=400, detail="缺少母版 Base Token 或链接")
    cmd = [
        _find_lark_cli(),
        "base", "+base-copy",
        "--as", "user",
        "--base-token", source_token,
        "--name", project_name,
        "--without-content",
    ]
    if folder_token:
        cmd.extend(["--folder-token", folder_token])
    if time_zone:
        cmd.extend(["--time-zone", time_zone])
    payload = await _run_lark_cli(cmd, timeout_sec=60)
    data = payload.get("data", {})
    base_obj = data.get("base", {}) if isinstance(data, dict) else {}
    token = str(
        (data.get("app_token") if isinstance(data, dict) else "")
        or (data.get("base_token") if isinstance(data, dict) else "")
        or (base_obj.get("base_token") if isinstance(base_obj, dict) else "")
        or ""
    )
    if not token:
        raise HTTPException(status_code=500, detail=f"复制 Base 成功但未返回 token: {data}")
    for attempt in range(12):
        try:
            await _run_lark_cli(
                [_find_lark_cli(), "base", "+table-list", "--as", "user", "--base-token", token],
                timeout_sec=15,
            )
            break
        except Exception:
            await asyncio.sleep(5)
    else:
        raise HTTPException(status_code=504, detail="复制 Base 后等待就绪超时（60秒）")
    return {"base_token": token, "template_base_token": source_token, "raw": data}


async def _list_base_tables(base_token: str) -> List[Dict[str, str]]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+table-list",
            "--as", "user",
            "--base-token", base_token,
        ],
        timeout_sec=30,
    )
    tables = payload.get("data", {}).get("tables", [])
    result: List[Dict[str, str]] = []
    for t in tables:
        if isinstance(t, dict) and t.get("name") and t.get("id"):
            result.append({"name": str(t["name"]), "id": str(t["id"])})
    return result


async def _get_base_info(base_token: str) -> Dict[str, Any]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+base-get",
            "--as", "user",
            "--base-token", base_token,
        ],
        timeout_sec=30,
    )
    return {
        "base_token": base_token,
        "name": _extract_base_name(payload),
        "raw": payload.get("data", {}),
    }


async def _read_xhs_cookies_from_cdp(cdp_base: str) -> Dict[str, Any]:
    import httpx as _httpx
    import json as _json
    import websockets
    from tools import utils as t_utils

    async with _httpx.AsyncClient() as http:
        targets_resp = await http.get(f"{cdp_base}/json", timeout=5)
        targets = targets_resp.json()

    usable_targets = [
        t for t in targets
        if isinstance(t, dict) and t.get("webSocketDebuggerUrl")
    ]
    xhs_target = next(
        (t for t in usable_targets if "xiaohongshu.com" in (t.get("url") or "")),
        usable_targets[0] if usable_targets else None,
    )
    if not xhs_target:
        raise HTTPException(status_code=400, detail="未找到可读取 Cookie 的浏览器页面，请先打开小红书页面")

    ws_url = xhs_target.get("webSocketDebuggerUrl")
    async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
        await ws.send(_json.dumps({
            "id": 1,
            "method": "Network.getCookies",
            "params": {"urls": ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]},
        }))
        resp = _json.loads(await ws.recv())

    cdp_cookies = resp.get("result", {}).get("cookies", [])
    cookie_list = [{"name": c["name"], "value": c["value"]} for c in cdp_cookies if c.get("name")]
    cookie_str, cookie_dict = t_utils.convert_cookies(cookie_list)
    return {
        "cookies": cookie_str,
        "cookie_dict": cookie_dict,
        "cookie_keys": list(cookie_dict.keys()),
        "targets": [str(t.get("url", ""))[:120] for t in targets[:8] if isinstance(t, dict)],
    }


async def _read_table_fields(base_token: str, table_id: str) -> List[str]:
    fields = await _read_table_field_defs(base_token, table_id)
    return [
        f.get("name")
        for f in fields
        if isinstance(f, dict) and f.get("name") and f.get("type") not in {
            "not_support", "attachment", "formula", "lookup", "auto_number",
            "created_at", "updated_at", "created_by", "updated_by",
            "created_time", "modified_time", "created_user", "modified_user",
        }
    ]


async def _read_table_field_defs(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+field-list",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--limit", "200",
        ],
        timeout_sec=30,
    )
    fields = payload.get("data", {}).get("fields", [])
    return [f for f in fields if isinstance(f, dict) and f.get("name")]


async def _create_base_field(base_token: str, table_id: str, field: Dict[str, Any]) -> None:
    command = [
        _find_lark_cli(),
        "base", "+field-create",
        "--as", "user",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", json.dumps(field, ensure_ascii=False),
    ]
    if field.get("type") == "formula":
        command.append("--i-have-read-guide")
    await _run_lark_cli(command, timeout_sec=45)


async def _update_base_field(base_token: str, table_id: str, field_id: str, field: Dict[str, Any]) -> None:
    await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+field-update",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--field-id", field_id,
            "--json", json.dumps(field, ensure_ascii=False),
            "--yes",
        ],
        timeout_sec=45,
    )


async def _set_table_view_field_order(
    base_token: str,
    table_id: str,
    ordered_field_names: List[str],
) -> None:
    """Keep customer-facing grid views aligned with the documented column order."""
    views = await _list_table_views(base_token, table_id)
    visible_fields = [name for name in ordered_field_names if name]
    if not visible_fields:
        return
    for view in views:
        if str(view.get("type") or "") != "grid" or not view.get("id"):
            continue
        await _set_view_visible_fields(base_token, table_id, str(view["id"]), visible_fields)


async def _list_table_views(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(), "base", "+view-list", "--as", "user", "--format", "json",
            "--base-token", base_token, "--table-id", table_id, "--limit", "200",
        ],
        timeout_sec=45,
    )
    views = ((payload.get("data") or {}).get("views") or [])
    return [view for view in views if isinstance(view, dict) and view.get("id")]


async def _set_view_visible_fields(
    base_token: str, table_id: str, view_id: str, visible_fields: List[str]
) -> None:
    try:
        await _run_lark_cli(
            [
                _find_lark_cli(), "base", "+view-set-visible-fields", "--as", "user",
                "--base-token", base_token, "--table-id", table_id,
                "--view-id", view_id,
                "--json", json.dumps({"visible_fields": visible_fields}, ensure_ascii=False),
            ],
            timeout_sec=45,
        )
    except HTTPException as exc:
        if not _is_lark_noop_error(exc):
            raise


def _is_lark_noop_error(exc: Exception) -> bool:
    detail = str(getattr(exc, "detail", exc) or "").lower()
    return "800070003" in detail or "no operation produced" in detail


async def _ensure_creator_selection_fields(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {field.get("name"): field for field in existing}
    wanted = _creator_selection_fields()
    for field in wanted:
        name = field["name"]
        current = existing_by_name.get(name)
        if current is None:
            await _create_base_field(base_token, table_id, field)
        elif (
            name in PGY_PLATFORM_NO_DATA_FIELDS
            and current.get("type") != field.get("type")
            and current.get("id")
        ):
            await _update_base_field(base_token, table_id, str(current["id"]), field)
    creator_type_field = next(field for field in wanted if field["name"] == "目标/推荐博主")
    existing_creator_type = existing_by_name.get("目标/推荐博主")
    if existing_creator_type and existing_creator_type.get("type") != "select" and existing_creator_type.get("id"):
        await _update_base_field(base_token, table_id, str(existing_creator_type["id"]), creator_type_field)
    # Existing old tables may already have text fields named 截图/详情文本.
    refreshed = await _read_table_field_defs(base_token, table_id)
    refreshed_by_name = {field.get("name"): field for field in refreshed}
    for legacy_name, attachment_name in [("截图", "截图附件"), ("详情文本", "详情文本附件")]:
        legacy = refreshed_by_name.get(legacy_name)
        if legacy and legacy.get("type") != "attachment" and attachment_name not in refreshed_by_name:
            await _create_base_field(base_token, table_id, _attachment_field(attachment_name))
    await _set_table_view_field_order(
        base_token,
        table_id,
        [field["name"] for field in wanted],
    )
    return await _read_table_field_defs(base_token, table_id)


def _text_field(name: str) -> Dict[str, Any]:
    return {"name": name, "type": "text", "style": {"type": "plain"}}


def _number_field(name: str) -> Dict[str, Any]:
    return {
        "name": name,
        "type": "number",
        "style": {
            "type": "plain",
            "precision": 0,
            "percentage": False,
            "thousands_separator": False,
        },
    }


def _datetime_field(name: str) -> Dict[str, Any]:
    return {"name": name, "type": "datetime", "style": {"format": "yyyy/MM/dd"}}


def _attachment_field(name: str) -> Dict[str, Any]:
    return {"name": name, "type": "attachment"}


def _formula_field(name: str, expression: str, description: str = "") -> Dict[str, Any]:
    field = {"name": name, "type": "formula", "expression": expression}
    if description:
        field["description"] = description
    return field


def _creator_type_field() -> Dict[str, Any]:
    return {
        "name": "目标/推荐博主",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "目标达人", "hue": "Blue", "lightness": "Lighter"},
            {"name": "相似博主", "hue": "Orange", "lightness": "Lighter"},
        ],
    }


def _account_monitor_status_field() -> Dict[str, Any]:
    return {
        "name": "蒲公英主页状态",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "有蒲公英主页", "hue": "Green", "lightness": "Lighter"},
            {"name": "无蒲公英主页", "hue": "Gray", "lightness": "Lighter"},
            {"name": "待人工确认", "hue": "Yellow", "lightness": "Lighter"},
        ],
    }


def _viral_monitor_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("归属项目"),
        _text_field("检索关键词"),
        _datetime_field("笔记发布时间"),
        _text_field("博主名"),
        _text_field("博主ID"),
        _text_field("博主主页"),
        _text_field("笔记类型"),
        _text_field("笔记标题"),
        _text_field("笔记ID"),
        _text_field("笔记链接"),
        _text_field("笔记内容"),
        _attachment_field("笔记封面"),
        _text_field("笔记图片1"),
        _text_field("笔记tag"),
        _number_field("点赞"),
        _number_field("收藏数"),
        _number_field("分享数"),
        _number_field("评论数"),
        _number_field("总互动数据（赞+藏+评，不算分享）"),
        _datetime_field("采集数据时间"),
        # Preserve the source URL for diagnostics while the customer-facing
        # cover column stores the actual attachment.
        _text_field("笔记封面URL"),
    ]


def _note_recreation_fields() -> List[Dict[str, Any]]:
    return [
        _number_field("收藏数"),
        _text_field("当日使用标记"),
        _text_field("改写打分"),
        _text_field("笔记ID"),
        _text_field("博主名"),
        _text_field("笔记链接"),
        _text_field("标题"),
        _datetime_field("采集时间"),
        _text_field("博主主页"),
        _text_field("标题改写.输出结果"),
        _attachment_field("封面改写"),
        _text_field("关键词"),
        _number_field("点赞数"),
        _number_field("评论数"),
        _text_field("内容"),
        _text_field("笔记类型"),
        _datetime_field("首发时间"),
        _number_field("分享数"),
        _number_field("博主粉丝数"),
        _attachment_field("封面附件"),
        _text_field("已使用账号记录"),
        _text_field("项目名"),
        _text_field("正文改写.输出结果"),
        _text_field("二次调整口令"),
        _text_field("二次标题改写"),
        _text_field("二次正文改写"),
        _text_field("二次图片改写"),
        _text_field("话题标签"),
    ]


async def _ensure_note_recreation_fields(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    """Add newly introduced display-only recreation fields to an existing project table."""
    existing = await _read_table_field_defs(base_token, table_id)
    existing_names = {str(field.get("name")) for field in existing if field.get("name")}
    for field in _note_recreation_fields():
        if field["name"] not in existing_names:
            await _create_base_field(base_token, table_id, field)
    return await _read_table_field_defs(base_token, table_id)


_NOTE_RECREATION_FIELD_ALIASES = {
    "project_name": ["项目名", "归属项目"],
    "keyword": ["关键词", "检索关键词", "来源关键词"],
    "original_title": ["标题", "笔记标题"],
    "original_body": ["内容", "笔记内容", "正文"],
    "original_images": ["封面附件", "封面图", "笔记封面", "图片", "原图"],
    "rewrite_title": ["标题改写.输出结果", "标题改写", "改写标题"],
    "rewrite_body": ["正文改写.输出结果", "正文改写", "内容改写", "改写正文"],
    "rewrite_images": ["封面改写", "图片改写", "图片改写结果"],
    "score": ["改写打分", "改写评分", "评分"],
    "adjustment_prompt": ["二次调整口令", "二次改写口令", "二次调整指令"],
    "second_title": ["二次标题改写", "二次改写标题"],
    "second_body": ["二次正文改写", "二次改写正文"],
    "second_images": ["二次图片改写", "二次图片改写结果"],
    "note_url": ["笔记链接", "发布笔记链接"],
}


def _recreation_cell_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        return "\n".join(_recreation_cell_text(item) for item in value.values() if item not in (None, ""))
    if isinstance(value, list):
        return "\n".join(_recreation_cell_text(item) for item in value if item not in (None, ""))
    return str(value).strip()


def _recreation_image_assets(value: Any) -> List[Dict[str, str]]:
    """Keep both remote URLs and Base attachment tokens for the Web UI."""
    assets: List[Dict[str, str]] = []

    def add_asset(url: str = "", file_token: str = "", name: str = "") -> None:
        identity = url or file_token
        if not identity or any((item.get("url") or item.get("file_token")) == identity for item in assets):
            return
        assets.append({"url": url, "file_token": file_token, "name": name})

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            urls = [str(item[key]).strip() for key in ("url", "tmp_url", "link", "download_url") if item.get(key)]
            for url in urls:
                add_asset(url=url)
            file_token = str(item.get("file_token") or "").strip()
            if file_token:
                add_asset(file_token=file_token, name=str(item.get("name") or "").strip())
            for key, nested in item.items():
                if key not in {"url", "tmp_url", "link", "download_url", "file_token", "name"}:
                    visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        else:
            for url in re.findall(r"https?://[^\s,，;；\]\)]+", _recreation_cell_text(item)):
                add_asset(url=url)

    visit(value)
    return assets


def _note_recreation_attachment_url(
    base_token: str, table_id: str, record_id: str, file_token: str, filename: str,
) -> str:
    return "/api/crawler/note-recreation/attachment?" + urlencode({
        "base_token": base_token,
        "table_id": table_id,
        "record_id": record_id,
        "file_token": file_token,
        "filename": filename,
    })


def _recreation_image_assets_with_preview(
    value: Any, base_token: str = "", table_id: str = "", record_id: str = "",
) -> List[Dict[str, str]]:
    assets = _recreation_image_assets(value)
    for asset in assets:
        if asset["file_token"] and record_id and base_token and table_id:
            asset["url"] = _note_recreation_attachment_url(
                base_token, table_id, record_id, asset["file_token"], asset["name"],
            )
    return assets


def _map_note_recreation_case(
    row: Dict[str, Any], base_token: str = "", table_id: str = "", record_id: str = "",
) -> Dict[str, Any]:
    def pick(key: str) -> Any:
        return next((row[name] for name in _NOTE_RECREATION_FIELD_ALIASES[key] if row.get(name) not in (None, "")), "")

    return {
        "project_name": _recreation_cell_text(pick("project_name")),
        "keyword": _recreation_cell_text(pick("keyword")),
        "original": {
            "title": _recreation_cell_text(pick("original_title")),
            "body": _recreation_cell_text(pick("original_body")),
            "images": _recreation_image_assets_with_preview(
                pick("original_images"), base_token, table_id, record_id,
            ),
            "image_note": _recreation_cell_text(pick("original_images")),
        },
        "rewrite": {
            "title": _recreation_cell_text(pick("rewrite_title")),
            "body": _recreation_cell_text(pick("rewrite_body")),
            "images": _recreation_image_assets_with_preview(
                pick("rewrite_images"), base_token, table_id, record_id,
            ),
            "image_note": _recreation_cell_text(pick("rewrite_images")),
        },
        "score": _recreation_cell_text(pick("score")),
        "second_adjustment": {
            "prompt": _recreation_cell_text(pick("adjustment_prompt")),
            "title": _recreation_cell_text(pick("second_title")),
            "body": _recreation_cell_text(pick("second_body")),
            "images": _recreation_image_assets_with_preview(
                pick("second_images"), base_token, table_id, record_id,
            ),
            "image_note": _recreation_cell_text(pick("second_images")),
        },
        "note_url": _recreation_cell_text(pick("note_url")),
    }


async def _read_note_recreation_cases(
    base_token: str, table_id: str, project_name: str = "", limit: int = 100,
) -> Dict[str, Any]:
    """Read only rewritten cases, with filtering performed by Base before rendering."""
    fields = await _read_table_field_defs(base_token, table_id)
    field_names = [str(field["name"]) for field in fields if field.get("name")]
    available = set(field_names)
    rewrite_fields = [name for name in _NOTE_RECREATION_FIELD_ALIASES["rewrite_images"] if name in available]
    if not rewrite_fields:
        return {"cases": [], "has_more": False, "fields": field_names}
    rewritten_filter = {"logic": "or", "conditions": [[name, "non_empty"] for name in rewrite_fields]}
    project_field = next((name for name in _NOTE_RECREATION_FIELD_ALIASES["project_name"] if name in available), "")
    filter_json: Dict[str, Any] = rewritten_filter
    if project_name.strip() and project_field:
        # Base filter JSON is one-level only. Project scoping is pushed down first;
        # the mapped rows below retain only rewritten cases.
        filter_json = {"logic": "and", "conditions": [[project_field, "==", project_name.strip()]]}
    cmd = [
        _find_lark_cli(), "base", "+record-list", "--as", "user", "--format", "json",
        "--base-token", base_token, "--table-id", table_id,
        "--filter-json", json.dumps(filter_json, ensure_ascii=False),
        "--limit", str(max(1, min(limit, 200))),
    ]
    for field_name in field_names:
        cmd.extend(["--field-id", field_name])
    payload = await _run_lark_cli(cmd, timeout_sec=60)
    data = payload.get("data") or {}
    response_fields = data.get("fields") or field_names
    cases = []
    rows = data.get("items") or data.get("records") or data.get("data") or []
    record_id_list = data.get("record_id_list") or []
    for row_index, values in enumerate(rows):
        record_id = str(record_id_list[row_index] or "") if row_index < len(record_id_list) else ""
        if isinstance(values, list):
            row = {name: value for name, value in zip(response_fields, values)}
        elif isinstance(values, dict):
            record_id = str(values.get("record_id") or values.get("id") or record_id)
            candidate_fields = values.get("fields")
            row = dict(candidate_fields) if isinstance(candidate_fields, dict) else values
        else:
            continue
        mapped = _map_note_recreation_case(row, base_token, table_id, record_id)
        if mapped["rewrite"]["images"]:
            cases.append(mapped)
    return {"cases": cases, "has_more": bool(data.get("has_more")), "fields": response_fields}


def _note_recreation_attachment_cache_path(
    base_token: str, table_id: str, record_id: str, file_token: str, filename: str,
) -> tuple[Path, str]:
    project_root = Path(__file__).resolve().parents[2]
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or f"{file_token}.bin"
    cache_key = hashlib.sha256(
        f"{base_token}\0{table_id}\0{record_id}\0{file_token}\0{safe_name}".encode("utf-8")
    ).hexdigest()
    return project_root / ".lark_attachment_cache" / cache_key / safe_name, cache_key


def _sentiment_monitor_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("项目名"),
        _text_field("笔记链接"),
        _text_field("笔记标题"),
        _number_field("点赞数"),
        _number_field("评论总数"),
        _formula_field("评论区敏感词", "\"\"", "评论内容实际命中的敏感词"),
        _formula_field("舆情风险", "\"\"", "笔记舆情监控自动汇总的风险类型"),
        _text_field("首评评论用户"),
        _text_field("IP属地"),
        _datetime_field("评论时间"),
        _text_field("评论内容"),
        _text_field("评论图片"),
        _number_field("评论点赞数"),
        _text_field("二级评论用户"),
        _text_field("三级评论用户"),
        _text_field("四级评论用户"),
    ]


def _comments_fields() -> List[Dict[str, Any]]:
    """Ordinary viral-note comments, kept separate from cooperation sentiment."""
    return [
        _text_field("IP属地"),
        _text_field("评论内容"),
        _text_field("评论用户"),
        _text_field("项目名"),
        _number_field("二级评论数"),
        _number_field("点赞数"),
        _datetime_field("评论时间"),
        _text_field("评论图片"),
        _text_field("笔记ID"),
        _text_field("关键词"),
        _text_field("父评论ID"),
        _text_field("评论区分析"),
    ]


async def _upsert_sentiment_formula_field(base_token: str, table_id: str, current: Dict[str, Any] | None, desired: Dict[str, Any]) -> None:
    """Create or update one Base formula field after its structure has been read."""
    command = [
        _find_lark_cli(),
        "base",
        "+field-create" if not current else "+field-update",
        "--as",
        "user",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
    ]
    if current:
        if not current.get("id"):
            raise HTTPException(status_code=400, detail=f"现有字段“{desired['name']}”缺少 field_id，无法更新公式")
        command.extend(["--field-id", str(current["id"]), "--yes"])
    command.extend(["--json", json.dumps(desired, ensure_ascii=False), "--i-have-read-guide"])
    await _run_lark_cli(command, timeout_sec=45)


async def _ensure_sentiment_monitor_fields(base_token: str, table_id: str, risk_groups: List[Any]) -> List[Dict[str, Any]]:
    """Keep writable comment fields and synchronize one formula column per risk group."""
    groups = _normalize_sentiment_risk_groups(risk_groups)
    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {str(field.get("name")): field for field in existing if field.get("name")}
    for field in _sentiment_monitor_fields():
        if field["name"] not in {"舆情风险", "评论区敏感词"} and field["name"] not in existing_by_name:
            await _create_base_field(base_token, table_id, field)

    summary = _formula_field("舆情风险", _sentiment_risk_formula(groups), "笔记舆情监控自动汇总的风险类型")
    await _upsert_sentiment_formula_field(base_token, table_id, existing_by_name.get("舆情风险"), summary)
    keyword_summary = _formula_field("评论区敏感词", _sentiment_keyword_formula(groups), "评论内容实际命中的敏感词")
    await _upsert_sentiment_formula_field(
        base_token,
        table_id,
        existing_by_name.get("评论区敏感词"),
        keyword_summary,
    )
    active_group_fields = set()
    for group in groups:
        name = _risk_group_field_name(group["name"])
        active_group_fields.add(name)
        desired = _formula_field(name, _risk_group_formula(group), "笔记舆情监控风险分组自动规则")
        await _upsert_sentiment_formula_field(base_token, table_id, existing_by_name.get(name), desired)

    # Preserve removed group columns for existing Base views, but clear their formula so deleted rules stop matching.
    for name, field in existing_by_name.items():
        description = str(field.get("description") or "")
        if (
            field.get("type") == "formula"
            and name.startswith("风险-")
            and "笔记舆情监控风险分组自动规则" in description
            and name not in active_group_fields
        ):
            disabled = _formula_field(name, "\"\"", "笔记舆情监控风险分组自动规则（已停用）")
            await _upsert_sentiment_formula_field(base_token, table_id, field, disabled)
    return await _read_table_field_defs(base_token, table_id)


PGY_PLATFORM_NO_DATA = "平台无数据"
PGY_PLATFORM_NO_DATA_FIELDS = {
    "内容类目（标签）", "合作行业",
    "粉丝数", "获赞收藏", "发布笔记数", "商业笔记数", "图文报价", "视频报价",
    "日常笔记曝光中位数", "日常笔记阅读中位数", "日常笔记互动中位数", "日常笔记互动率",
    "日常笔记中位点赞量", "日常笔记中位收藏量", "日常笔记中位评论量",
    "日常笔记中位分享量", "日常笔记中位关注量", "日常笔记视频完播率", "日常笔记图文3秒阅读率",
    "合作笔记曝光中位数", "合作笔记阅读中位数", "合作笔记互动中位数", "合作笔记互动率",
    "合作笔记中位点赞量", "合作笔记中位收藏量", "合作笔记中位评论量",
    "合作笔记中位分享量", "合作笔记中位关注量", "合作笔记视频完播率", "合作笔记图文3秒阅读率",
    "粉丝增量", "千赞笔记比例", "百赞笔记比例", "活跃粉丝占比", "阅读粉丝占比", "互动粉丝占比",
    "近7日活跃天数", "地区", "邀约数", "响应率", "粉丝增长率", "付费粉丝占比",
    "女性粉丝占比", "男性粉丝占比", "主要年龄段", "省份TOP5", "城市TOP5", "兴趣TOP8",
}


def _creator_selection_fields() -> List[Dict[str, Any]]:
    return [
        _creator_type_field(),
        _number_field("推荐排名"),
        _text_field("目标达人昵称"),
        _text_field("达人昵称"),
        _text_field("小红书号"),
        _text_field("主页链接"),
        _text_field("蒲公英主页链接"),
        _text_field("内容类目（标签）"),
        _text_field("合作行业"),
        _text_field("粉丝数"),
        _text_field("获赞收藏"),
        _text_field("发布笔记数"),
        _text_field("商业笔记数"),
        _text_field("图文报价"),
        _text_field("视频报价"),
        *_pgy_metric_fields(),
        _text_field("粉丝增量"),
        _text_field("千赞笔记比例"),
        _text_field("百赞笔记比例"),
        _text_field("活跃粉丝占比"),
        _text_field("阅读粉丝占比"),
        _text_field("互动粉丝占比"),
        _text_field("近7日活跃天数"),
        _text_field("地区"),
        _text_field("邀约数"),
        _text_field("响应率"),
        _text_field("粉丝增长率"),
        _text_field("付费粉丝占比"),
        _text_field("女性粉丝占比"),
        _text_field("男性粉丝占比"),
        _text_field("主要年龄段"),
        _text_field("省份TOP5"),
        _text_field("城市TOP5"),
        _text_field("兴趣TOP8"),
        _datetime_field("最新笔记更新时间"),
        _datetime_field("采集博主数据日期"),
    ]


def _pgy_metric_fields() -> List[Dict[str, Any]]:
    prefixes = ("日常笔记", "合作笔记")
    metrics: List[Dict[str, Any]] = []
    for prefix in prefixes:
        metrics.extend([
            _text_field(f"{prefix}曝光中位数"),
            _text_field(f"{prefix}阅读中位数"),
            _text_field(f"{prefix}互动中位数"),
            _text_field(f"{prefix}互动率"),
            _text_field(f"{prefix}中位点赞量"),
            _text_field(f"{prefix}中位收藏量"),
            _text_field(f"{prefix}中位评论量"),
            _text_field(f"{prefix}中位分享量"),
            _text_field(f"{prefix}中位关注量"),
            _text_field(f"{prefix}视频完播率"),
            _text_field(f"{prefix}图文3秒阅读率"),
        ])
    return metrics


def _account_content_monitor_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("达人昵称"),
        _text_field("小红书号"),
        _text_field("主页链接"),
        _text_field("蒲公英主页链接"),
        _account_monitor_status_field(),
        _text_field("蒲公英查询依据"),
        _datetime_field("发布笔记倒序（发布时间由近及远）"),
        _text_field("笔记链接"),
        _text_field("笔记标题"),
        _text_field("笔记内容"),
        _text_field("笔记封面"),
        _text_field("笔记tag"),
        _number_field("点赞"),
        _number_field("收藏"),
        _number_field("评论"),
        _number_field("转发量"),
        _number_field("笔记总互动量（点赞+收藏+评论）"),
        _number_field("曝光量"),
        _number_field("阅读量"),
        _number_field("笔记收获关注量"),
        _text_field("内容类目（标签）"),
        _text_field("合作行业"),
        _number_field("粉丝数"),
        _number_field("获赞收藏"),
        _number_field("发布笔记数"),
        _number_field("商业笔记数"),
        _number_field("图文报价"),
        _number_field("视频报价"),
        *_pgy_metric_fields(),
        _number_field("粉丝增量"),
        _text_field("千赞笔记比例"),
        _text_field("百赞笔记比例"),
        _text_field("活跃粉丝占比"),
        _text_field("阅读粉丝占比"),
        _text_field("互动粉丝占比"),
        _number_field("近7日活跃天数"),
        _text_field("地区"),
        _number_field("邀约数"),
        _text_field("响应率"),
        _text_field("粉丝增长率"),
        _text_field("付费粉丝占比"),
        _text_field("女性粉丝占比"),
        _text_field("男性粉丝占比"),
        _text_field("主要年龄段"),
        _text_field("省份TOP5"),
        _text_field("城市TOP5"),
        _text_field("兴趣TOP8"),
        _datetime_field("最新笔记更新时间"),
        _datetime_field("采集博主数据日期"),
    ]


def _account_monitor_compact_view_fields() -> List[str]:
    """Fields that remain useful when Pugongying metrics are unavailable."""
    public_fields = _account_content_monitor_public_field_names()
    return [
        "达人昵称", "小红书号", "主页链接", "蒲公英主页状态", "蒲公英查询依据",
        *[
            name for name in public_fields
            if name not in {"达人昵称", "小红书号", "主页链接", "蒲公英主页链接"}
        ],
    ]


async def _ensure_account_content_monitor_fields(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    """Repair an existing template table and keep status views idempotent."""
    wanted = _account_content_monitor_fields()
    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {str(field.get("name")): field for field in existing if field.get("name")}
    for field in wanted:
        if field["name"] not in existing_by_name:
            await _create_base_field(base_token, table_id, field)

    desired_status = _account_monitor_status_field()
    existing_status = existing_by_name.get("蒲公英主页状态")
    if existing_status and existing_status.get("id"):
        current_options = {
            str(option.get("name"))
            for option in (existing_status.get("options") or [])
            if isinstance(option, dict) and option.get("name")
        }
        desired_options = {str(option["name"]) for option in desired_status["options"]}
        if existing_status.get("type") != "select" or not desired_options.issubset(current_options):
            await _update_base_field(
                base_token, table_id, str(existing_status["id"]), desired_status,
            )

    refreshed = await _read_table_field_defs(base_token, table_id)
    refreshed_by_name = {str(field.get("name")): field for field in refreshed if field.get("name")}
    status_field = refreshed_by_name.get("蒲公英主页状态")
    if not status_field or not status_field.get("id"):
        raise HTTPException(status_code=400, detail="账号内容监测表缺少蒲公英主页状态字段")

    all_fields = [field["name"] for field in wanted]
    status_views = {
        "有蒲公英主页": all_fields,
        "无蒲公英主页": _account_monitor_compact_view_fields(),
        "待人工确认": _account_monitor_compact_view_fields(),
    }
    views = await _list_table_views(base_token, table_id)
    for view in views:
        if (
            str(view.get("type") or "") == "grid"
            and view.get("id")
            and str(view.get("name") or "") not in status_views
        ):
            await _set_view_visible_fields(
                base_token, table_id, str(view["id"]), all_fields,
            )
    views_by_name = {str(view.get("name")): view for view in views if view.get("name")}
    for view_name in status_views:
        if view_name in views_by_name:
            continue
        await _run_lark_cli(
            [
                _find_lark_cli(), "base", "+view-create", "--as", "user",
                "--base-token", base_token, "--table-id", table_id,
                "--json", json.dumps({"name": view_name, "type": "grid"}, ensure_ascii=False),
            ],
            timeout_sec=45,
        )

    views = await _list_table_views(base_token, table_id)
    views_by_name = {str(view.get("name")): view for view in views if view.get("name")}
    status_field_id = str(status_field["id"])
    for view_name, visible_fields in status_views.items():
        view = views_by_name.get(view_name)
        if not view or not view.get("id"):
            raise HTTPException(status_code=400, detail=f"未能创建账号内容监测视图：{view_name}")
        view_id = str(view["id"])
        try:
            await _run_lark_cli(
                [
                    _find_lark_cli(), "base", "+view-set-filter", "--as", "user",
                    "--base-token", base_token, "--table-id", table_id, "--view-id", view_id,
                    "--json", json.dumps(
                        {
                            "logic": "and",
                            "conditions": [[status_field_id, "intersects", [view_name]]],
                        },
                        ensure_ascii=False,
                    ),
                ],
                timeout_sec=45,
            )
        except HTTPException as exc:
            if not _is_lark_noop_error(exc):
                raise
        await _set_view_visible_fields(base_token, table_id, view_id, visible_fields)
    return refreshed


def _creator_screening_result_fields() -> List[Dict[str, Any]]:
    """AI initial-screening output keeps exactly the four imported columns."""
    return [
        _text_field("达人昵称"),
        _text_field("博主ID"),
        _text_field("主页链接"),
        _text_field("达人价格"),
    ]


async def _ensure_fields_and_view_order(
    base_token: str,
    table_id: str,
    fields: List[Dict[str, Any]],
    *,
    hidden_fields: set[str] | None = None,
) -> None:
    existing = await _read_table_field_defs(base_token, table_id)
    existing_names = {str(field.get("name")) for field in existing if field.get("name")}
    for field in fields:
        if field["name"] not in existing_names:
            await _create_base_field(base_token, table_id, field)
    hidden = hidden_fields or set()
    await _set_table_view_field_order(
        base_token,
        table_id,
        [field["name"] for field in fields if field["name"] not in hidden],
    )


async def _ensure_viral_monitor_fields(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    """Migrate the cover URL safely and expose the real attachment as 笔记封面."""
    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {str(field.get("name")): field for field in existing if field.get("name")}
    current_cover = existing_by_name.get("笔记封面")
    current_cover_url = existing_by_name.get("笔记封面URL")

    if current_cover and current_cover.get("type") != "attachment" and current_cover.get("id"):
        url_field_name = "笔记封面URL" if not current_cover_url else "笔记封面URL（旧）"
        await _update_base_field(
            base_token, table_id, str(current_cover["id"]), _text_field(url_field_name),
        )

    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {str(field.get("name")): field for field in existing if field.get("name")}
    current_cover = existing_by_name.get("笔记封面")
    legacy_attachment = existing_by_name.get("封面文件")
    if not current_cover or current_cover.get("type") != "attachment":
        if legacy_attachment and legacy_attachment.get("type") == "attachment" and legacy_attachment.get("id"):
            await _update_base_field(
                base_token, table_id, str(legacy_attachment["id"]), _attachment_field("笔记封面"),
            )
        else:
            await _create_base_field(base_token, table_id, _attachment_field("笔记封面"))

    wanted = _viral_monitor_fields()
    existing = await _read_table_field_defs(base_token, table_id)
    existing_names = {str(field.get("name")) for field in existing if field.get("name")}
    for field in wanted:
        if field["name"] not in existing_names:
            await _create_base_field(base_token, table_id, field)

    await _set_table_view_field_order(
        base_token,
        table_id,
        [
            field["name"] for field in wanted
            if field["name"] not in {"笔记图片1", "笔记封面URL"}
        ],
    )
    return await _read_table_field_defs(base_token, table_id)


def _account_content_monitor_public_field_names() -> List[str]:
    """The public-profile report deliberately contains only fields available without Pugongying."""
    return [
        "达人昵称", "小红书号", "主页链接", "蒲公英主页链接",
        "发布笔记倒序（发布时间由近及远）", "笔记链接", "笔记标题", "笔记封面",
        "笔记tag", "点赞", "收藏", "评论", "笔记总互动量（点赞+收藏+评论）",
    ]


def _note_data_monitor_fields() -> List[Dict[str, Any]]:
    return [
        _number_field("序号"),
        _text_field("达人昵称"),
        _text_field("小红书id"),
        _text_field("发布笔记链接"),
        _datetime_field("发布时间"),
        _text_field("笔记tag"),
        _text_field("笔记标题"),
        _number_field("点赞"),
        _number_field("收藏"),
        _number_field("评论"),
        _number_field("总互动（点赞+收藏+评论）"),
        _number_field("分享"),
        _number_field("曝光量"),
        _number_field("阅读量"),
        _text_field("笔记失效/正常（有失效链接作标记）"),
    ]


def _latest_local_file(
    data_type: str,
    crawler_type_hint: str = "",
    *,
    modified_after: float | None = None,
    strict_mode: bool = False,
) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    suffix = "contents" if data_type == "notes" else "comments"
    data_roots = [data_dir() / "xhs"]
    legacy_data_root = project_root / "data" / "xhs"
    if legacy_data_root.resolve() != data_roots[0].resolve():
        data_roots.append(legacy_data_root)
    mode = (crawler_type_hint or "").strip()

    def collect(patterns: List[tuple[str, str]]) -> List[Path]:
        candidates: List[Path] = []
        for data_root in data_roots:
            for folder, pattern in patterns:
                dir_path = data_root / folder
                if not dir_path.exists():
                    continue
                for candidate in dir_path.glob(pattern):
                    try:
                        if modified_after is not None and candidate.stat().st_mtime < modified_after:
                            continue
                    except OSError:
                        continue
                    candidates.append(candidate)
        return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)

    if mode:
        mode_candidates = collect([
            ("jsonl", f"{mode}_{suffix}_*.jsonl"),
            ("csv", f"{mode}_{suffix}_*.csv"),
            ("json", f"{mode}_{suffix}_*.json"),
            ("excel", f"{mode}_{suffix}_*.xlsx"),
            ("excel", f"{mode}_{suffix}_*.xls"),
        ])
        if mode_candidates:
            return mode_candidates[0]
        if strict_mode:
            raise HTTPException(status_code=404, detail=f"未找到本地 {mode} 模式的 {data_type} 数据文件")

    fallback_candidates = collect([
        ("jsonl", f"*_{suffix}_*.jsonl"),
        ("csv", f"*_{suffix}_*.csv"),
        ("json", f"*_{suffix}_*.json"),
        ("excel", f"*_{suffix}_*.xlsx"),
        ("excel", f"*_{suffix}_*.xls"),
    ])
    if not fallback_candidates:
        raise HTTPException(status_code=404, detail=f"未找到本地 {data_type} 数据文件（jsonl/csv/json/xlsx/xls）")
    return fallback_candidates[0]


def _read_jsonl_rows(file_path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _read_local_rows(file_path: Path) -> List[Dict[str, Any]]:
    """
    Read local rows from jsonl/json/csv/xlsx/xls into list[dict].
    """
    suffix = file_path.suffix.lower()
    if suffix == ".jsonl":
        return _read_jsonl_rows(file_path)
    if suffix == ".json":
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [x for x in payload if isinstance(x, dict)]
            return [payload] if isinstance(payload, dict) else []
        except Exception:
            return []
    if suffix == ".csv":
        rows: List[Dict[str, Any]] = []
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if isinstance(row, dict):
                    rows.append(dict(row))
        return rows
    if suffix in (".xlsx", ".xls"):
        try:
            import pandas as pd  # type: ignore
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取 Excel 失败：缺少 pandas/openpyxl 依赖。{e}")
        try:
            df = pd.read_excel(file_path)
            if df is None or df.empty:
                return []
            return df.where(pd.notnull(df), "").to_dict(orient="records")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"读取 Excel 失败：{e}")
    raise HTTPException(status_code=400, detail=f"不支持的文件类型：{file_path.suffix}，仅支持 jsonl/json/csv/xlsx/xls")


def _related_notes_file(comment_file: Path, crawler_type_hint: str = "") -> Path:
    related_name = re.sub(r"_comments_", "_contents_", comment_file.name, count=1)
    if related_name != comment_file.name:
        related_file = comment_file.with_name(related_name)
        if related_file.exists():
            return related_file
    return _latest_local_file("notes", crawler_type_hint)


def _account_monitor_public_row(source: Dict[str, Any]) -> Dict[str, Any]:
    fields = _account_content_monitor_public_field_names()
    return dict(zip(fields, _row_to_table_values(source, fields, "notes")))


def _account_monitor_pgy_row(
    source: Dict[str, Any], summary: Dict[str, Any] | None, lookup: Dict[str, str] | None = None
) -> Dict[str, Any]:
    """Combine one public note with account-level Pugongying metrics.

    Pugongying exposes creator metrics, while the public crawler exposes the
    individual note. Repeating the creator metrics for each of the 10 notes
    keeps the report flat and matches the customer-provided sheet layout.
    """
    fields = [field["name"] for field in _account_content_monitor_fields()]
    result: Dict[str, Any] = {}
    if summary:
        metrics = dict(summary.get("target_metrics") or {})
        # pgy_automation uses fans_num internally, while the reusable Base
        # mapper historically calls the same metric fans_count.
        if metrics.get("fans_num") not in (None, ""):
            metrics.setdefault("fans_count", metrics["fans_num"])
        result.update(dict(zip(fields, _pgy_row_to_values(metrics, fields))))
        result["蒲公英主页链接"] = summary.get("url") or result.get("蒲公英主页链接", "")
        result["小红书号"] = summary.get("red_id") or result.get("小红书号", "")
        result["达人昵称"] = summary.get("nickname") or result.get("达人昵称", "")
    # Only note-level public fields take priority. Do not map the whole 68
    # fields from the public crawler, otherwise missing Pugongying metrics are
    # converted to zero and overwrite the backend values.
    result.update(_account_monitor_public_row(source))
    # A Pugongying lookup can provide the proper red ID and its own detail-page
    # URL even when the public crawler only returned the profile user ID.
    if summary:
        result["蒲公英主页链接"] = summary.get("url") or result.get("蒲公英主页链接", "")
        result["小红书号"] = summary.get("red_id") or result.get("小红书号", "")
    if lookup:
        result["蒲公英主页状态"] = lookup.get("status") or "待人工确认"
        result["蒲公英查询依据"] = lookup.get("evidence") or ""
    return result


def _account_monitor_creator_key(row: Dict[str, Any]) -> str:
    return str(
        row.get("author_user_id")
        or row.get("user_id")
        or row.get("账号ID")
        or row.get("author_nickname")
        or row.get("nickname")
        or ""
    ).strip().lower()


def _account_monitor_creator_name(row: Dict[str, Any]) -> str:
    return str(row.get("author_nickname") or row.get("nickname") or row.get("达人昵称") or "").strip()


def _account_monitor_creator_id(row: Dict[str, Any]) -> str:
    creator_id = str(row.get("author_user_id") or row.get("user_id") or row.get("账号ID") or "").strip().lower()
    if creator_id:
        return creator_id
    profile_url = str(row.get("author_homepage_url") or row.get("author_profile_url") or row.get("主页链接") or "").strip()
    return _profile_id_from_url(profile_url) if profile_url else ""


def _write_account_monitor_report(rows: List[Dict[str, Any]], report_mode: str, job_id: str) -> Path:
    fields = (
        [field["name"] for field in _account_content_monitor_fields()]
        if report_mode in {"pgy", "auto"}
        else _account_content_monitor_public_field_names()
    )
    # The workbook is an implementation detail used for Base synchronization;
    # the customer-facing result lives in the Web UI and Feishu Base only.
    report_root = temp_dir() / "account_monitor"
    report_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "蒲公英自动判断" if report_mode == "auto" else ("含蒲公英" if report_mode == "pgy" else "公开数据")
    output_path = report_root / f"账号内容监测_{suffix}_{timestamp}_{job_id[:8]}.xlsx"
    try:
        import pandas as pd  # type: ignore

        pd.DataFrame(rows, columns=fields).to_excel(output_path, index=False)
    except Exception as exc:
        raise RuntimeError(f"生成账号内容监测 Excel 失败：{exc}") from exc
    return output_path


async def _build_account_monitor_report(
    job_id: str,
    report_mode: str,
    base_token: str = "",
    table_id: str = "",
    *,
    resume_pgy_only: bool = False,
) -> None:
    job = account_monitor_jobs[job_id]
    try:
        if resume_pgy_only:
            job.update({"status": "enriching", "stage": "正在从蒲公英断点继续", "error": "", "pgy_login_required": False})
            source_path = Path(str(job.get("source_path") or ""))
            if not source_path.is_file():
                raise RuntimeError("小红书抓取结果已不存在，无法从蒲公英断点继续，请重新执行账号内容监测")
        else:
            notes_per_creator = 10
            job.update({"status": "crawling", "stage": f"正在抓取各账号最近 {notes_per_creator} 篇笔记", "error": ""})
            if not await _wait_crawler_idle(timeout_sec=1800):
                raise RuntimeError("等待账号笔记抓取完成超时")
            source_started_at = float(job.get("source_started_at") or 0)
            try:
                source_path = _latest_local_file(
                    "notes",
                    "creator",
                    modified_after=source_started_at or None,
                    strict_mode=True,
                )
            except HTTPException as exc:
                if exc.status_code == 404:
                    raise RuntimeError("本次账号未生成新的小红书笔记数据，请检查小红书登录状态后重试") from exc
                raise
            job["source_path"] = str(source_path)
        source_rows = _read_local_rows(source_path)
        if not source_rows:
            raise RuntimeError("抓取完成但没有可生成报表的笔记数据")

        requested_creator_ids = {
            str(item).strip().lower()
            for item in (job.get("requested_creator_ids") or [])
            if str(item).strip()
        }
        if requested_creator_ids:
            all_source_rows = source_rows
            source_rows = [
                row for row in all_source_rows
                if _account_monitor_creator_id(row) in requested_creator_ids
            ]
            job["ignored_historical_row_count"] = len(all_source_rows) - len(source_rows)
            if not source_rows:
                raise RuntimeError("本次抓取未返回所填账号的数据，已阻止使用历史账号数据，请检查小红书登录状态后重试")

        summaries: Dict[str, Dict[str, Any]] = dict(job.get("_pgy_summaries") or {}) if resume_pgy_only else {}
        pgy_lookups: Dict[str, Dict[str, str]] = dict(job.get("_pgy_lookups") or {}) if resume_pgy_only else {}
        pgy_errors: List[str] = []
        pgy_login_accounts: List[str] = []
        if report_mode in {"pgy", "auto"}:
            creators: Dict[str, str] = {}
            for row in source_rows:
                key = _account_monitor_creator_key(row)
                name = _account_monitor_creator_name(row)
                if key and name:
                    creators.setdefault(key, name)
            if not creators:
                raise RuntimeError("公开笔记数据中未识别到达人昵称，无法匹配蒲公英数据")
            creator_items = list(creators.items())
            pending_keys = {
                str(item).strip().lower()
                for item in (job.get("_pgy_pending_keys") or [])
                if str(item).strip()
            }
            if resume_pgy_only:
                creator_items = [item for item in creator_items if item[0] in pending_keys]
                if not creator_items:
                    raise RuntimeError("没有可继续的蒲公英断点，请重新执行账号内容监测")
                for pending_key in pending_keys:
                    summaries.pop(pending_key, None)
                    pgy_lookups.pop(pending_key, None)
            total = len(creator_items)
            for index, (key, nickname) in enumerate(creator_items, start=1):
                stage_prefix = "正在从断点继续蒲公英指标" if resume_pgy_only else "正在补充蒲公英指标"
                job.update({"status": "enriching", "stage": f"{stage_prefix}（{index}/{total}）：{nickname}"})
                result = await _run_pgy_automation(
                    ["run-kol", "--nickname", nickname, "--similar-detail-limit", "0"],
                    timeout_sec=240,
                )
                summary_path = ((result.get("outputs") or {}).get("summary") or "").strip()
                if result.get("status") == "not_found":
                    pgy_lookups[key] = {
                        "status": "无蒲公英主页",
                        "evidence": f"蒲公英“找博主”按昵称“{nickname}”查询：暂无结果",
                    }
                    job.update({"_pgy_summaries": summaries, "_pgy_lookups": pgy_lookups})
                    continue
                if result.get("status") == "error" or result.get("returncode") or not summary_path:
                    reason = str(result.get("error") or "未返回蒲公英数据")[:160]
                    pgy_lookups[key] = {"status": "待人工确认", "evidence": f"蒲公英查询未完成：{reason}"}
                    pgy_errors.append(f"{nickname}: {reason}")
                    if _pgy_login_required(result):
                        remaining_creators = creator_items[index:]
                        pgy_login_accounts = [nickname, *(name for _, name in remaining_creators)]
                        pending_keys = [key, *(remaining_key for remaining_key, _ in remaining_creators)]
                        for remaining_key, remaining_name in remaining_creators:
                            pending_reason = "蒲公英需要登录，待登录后重新判断"
                            pgy_lookups[remaining_key] = {
                                "status": "待人工确认",
                                "evidence": pending_reason,
                            }
                            pgy_errors.append(f"{remaining_name}: {pending_reason}")
                        job.update({
                            "stage": "蒲公英需要登录，请登录后重新判断",
                            "pgy_login_required": True,
                            "pgy_login_accounts": pgy_login_accounts,
                            "pgy_errors": pgy_errors,
                            "pgy_review_count": len(pgy_login_accounts),
                            "_pgy_summaries": summaries,
                            "_pgy_lookups": pgy_lookups,
                            "_pgy_pending_keys": pending_keys,
                        })
                        break
                    job.update({"_pgy_summaries": summaries, "_pgy_lookups": pgy_lookups})
                    continue
                try:
                    summaries[key] = json.loads(Path(summary_path).read_text(encoding="utf-8"))
                    pgy_lookups[key] = {
                        "status": "有蒲公英主页",
                        "evidence": f"蒲公英“找博主”按昵称“{nickname}”查询到结果",
                    }
                except Exception as exc:
                    pgy_lookups[key] = {"status": "待人工确认", "evidence": f"读取蒲公英查询结果失败：{exc}"}
                    pgy_errors.append(f"{nickname}: 读取蒲公英结果失败（{exc}）")
                job.update({"_pgy_summaries": summaries, "_pgy_lookups": pgy_lookups})

        job.update({"status": "exporting", "stage": "正在生成账号内容监测表"})
        report_rows = [
            _account_monitor_pgy_row(
                row, summaries.get(_account_monitor_creator_key(row)), pgy_lookups.get(_account_monitor_creator_key(row))
            )
            if report_mode in {"pgy", "auto"}
            else _account_monitor_public_row(row)
            for row in source_rows
        ]
        report_path = _write_account_monitor_report(report_rows, report_mode, job_id)
        sync_result: Dict[str, Any] = {"status": "skipped", "reason": "未绑定账号内容监测表"}
        if base_token and table_id:
            job.update({"status": "syncing", "stage": "正在同步至账号内容监测表"})
            sync_result = await sync_local_to_base(
                LocalToBaseSyncRequest(
                    base_token=base_token,
                    table_id=table_id,
                    data_type="notes",
                    crawler_type_hint="creator",
                    file_path=str(report_path),
                )
            )
        job.update({
            "status": "completed",
            "stage": "账号内容监测表已同步" if sync_result.get("status") == "ok" else "账号内容监测表已生成",
            "report_path": str(report_path),
            "row_count": len(report_rows),
            "source_path": str(source_path),
            "pgy_errors": pgy_errors,
            "pgy_login_required": bool(pgy_login_accounts),
            "pgy_login_accounts": pgy_login_accounts,
            "pgy_found_count": sum(1 for item in pgy_lookups.values() if item.get("status") == "有蒲公英主页"),
            "pgy_not_found_count": sum(1 for item in pgy_lookups.values() if item.get("status") == "无蒲公英主页"),
            "pgy_review_count": sum(1 for item in pgy_lookups.values() if item.get("status") == "待人工确认"),
            "sync_result": sync_result,
            "_pgy_summaries": summaries,
            "_pgy_lookups": pgy_lookups,
            "_pgy_pending_keys": list(job.get("_pgy_pending_keys") or []) if pgy_login_accounts else [],
        })
    except Exception as exc:
        job.update({"status": "error", "stage": "生成失败", "error": str(exc)})


def _row_to_table_values(row: Dict[str, Any], table_fields: List[str], data_type: str) -> List[Any]:
    alias_map = {
        "标题": ["title", "笔记标题"],
        "笔记标题": ["title", "标题"],
        "内容": ["desc", "content", "note_content"],
        "笔记内容": ["desc", "content", "note_content", "内容"],
        "博主名": ["author_nickname", "nickname"],
        "达人昵称": ["author_nickname", "nickname", "博主名", "账号"],
        "账号": ["author_nickname", "nickname", "博主名"],
        "账号名称": ["author_nickname", "nickname", "博主名", "账号"],
        "账号ID": ["author_user_id", "user_id", "author_id"],
        "博主ID": ["author_user_id", "user_id", "author_id", "账号ID"],
        "小红书id": ["author_user_id", "user_id", "author_id", "账号ID", "小红书号"],
        "小红书ID": ["author_user_id", "user_id", "author_id", "账号ID"],
        "小红书号": ["author_user_id", "user_id", "author_id", "账号ID"],
        "账号主页": ["author_homepage_url", "author_profile_url", "博主主页"],
        "博主主页": ["author_homepage_url", "author_profile_url"],
        "主页链接": ["author_homepage_url", "author_profile_url", "博主主页", "账号主页"],
        "笔记链接": ["note_url"],
        "发布笔记链接": ["note_url", "笔记链接"],
        "发布链接": ["note_url", "笔记链接"],
        "笔记ID": ["note_id", "id"],
        "归属项目": ["project_name", "项目名", "所属项目"],
        "项目名": ["project_name", "归属项目", "所属项目"],
        "关键词": ["source_keyword", "搜索关键词"],
        "检索关键词": ["source_keyword", "搜索关键词", "关键词"],
        "搜索关键词": ["source_keyword"],
        "笔记类型": ["note_type", "type"],
        "封面图": ["image_list", "cover", "cover_url"],
        "笔记封面": ["cover", "cover_url", "image_list", "封面图"],
        "笔记封面URL": ["cover", "cover_url", "image_list", "封面图"],
        "笔记图片1": ["image_list", "images", "img_urls"],
        "话题标签": ["tag_list", "topics"],
        "笔记tag": ["tag_list", "topics", "话题标签", "语义标签"],
        "点赞量": ["liked_count", "like_count", "点赞数"],
        "点赞数": ["liked_count", "like_count", "点赞量"],
        "评论点赞数": ["comment_like_count", "like_count", "点赞数"],
        "点赞": ["liked_count", "like_count", "点赞量", "点赞数"],
        "收藏量": ["collected_count", "收藏数"],
        "收藏数": ["collected_count", "收藏量"],
        "收藏": ["collected_count", "收藏量", "收藏数"],
        "评论量": ["comment_count", "评论数"],
        "评论数": ["comment_count", "评论量"],
        "评论": ["comment_count", "评论量", "评论数"],
        "分享量": ["share_count", "分享数"],
        "分享数": ["share_count", "分享量"],
        "分享": ["share_count", "分享量", "分享数"],
        "转发量": ["share_count", "分享量", "分享数"],
        "阅读量": ["read_count", "view_count", "浏览量"],
        "曝光量": ["exposure_count", "imp", "曝光量"],
        "发布日期": ["publish_date", "发布时间", "time", "create_time"],
        "发布时间": ["publish_date", "发布时间", "time", "create_time", "首发时间"],
        "笔记发布时间": ["publish_date", "发布时间", "time", "create_time", "首发时间"],
        "发布笔记倒序（发布时间由近及远）": ["publish_date", "发布时间", "time", "create_time", "首发时间"],
        "博主粉丝数": ["author_fans", "author_fans_count", "fans_count"],
        "首发时间": ["time", "create_time", "发布时间"],
        "采集时间": ["last_modify_ts", "crawl_time", "抓取时间", "last_update_time"],
        "采集数据时间": ["last_modify_ts", "crawl_time", "抓取时间", "采集时间", "last_update_time"],
        "评论ID": ["comment_id"],
        "评论内容": ["content"],
        "评论用户": ["comment_user_nickname", "nickname"],
        "首评评论用户": ["comment_user_nickname", "nickname", "评论用户"],
        "二级评论用户": ["comment_user_nickname", "nickname", "评论用户"],
        "三级评论用户": ["comment_user_nickname", "nickname", "评论用户"],
        "四级评论用户": ["comment_user_nickname", "nickname", "评论用户"],
        "评论用户ID": ["comment_user_id", "user_id"],
        "评论时间": ["create_time"],
        "IP属地": ["ip_location"],
        "二级评论数": ["sub_comment_count"],
        "父评论ID": ["parent_comment_id"],
        "评论图片": ["pictures"],
        "头像": ["avatar"],
        "author_nickname": ["nickname"], "author_user_id": ["user_id"], "comment_user_id": ["user_id"], "comment_user_nickname": ["nickname"],
    }
    datetime_fields = {"采集时间", "采集数据时间", "首发时间", "笔记发布时间", "发布时间", "发布笔记倒序（发布时间由近及远）", "评论时间", "create_time", "last_modify_ts"}
    numeric_fields = {
        "liked_count", "collected_count", "comment_count", "share_count", "like_count",
        "点赞量", "收藏量", "评论量", "分享量",
        "点赞数", "收藏数", "评论数", "分享数",
        "点赞", "收藏", "评论", "分享", "转发量", "阅读量", "曝光量", "互动总和", "发布日期",
        "博主粉丝数", "二级评论数", "序号", "评论总数", "笔记收获关注量",
    }
    values: List[Any] = []
    for field_name in table_fields:
        candidates = [field_name] + alias_map.get(field_name, [])
        value: Any = ""
        for c in candidates:
            if c in row and row.get(c) not in (None, ""):
                value = row.get(c)
                break
        if field_name == "stage" and value == "":
            value = "trial_notes" if data_type == "notes" else "trial_comments"
        if field_name == "媒介进度" and value == "":
            value = "已发布"
        if field_name == "笔记类型" and value not in ("", None):
            note_type_text = str(value).lower()
            if note_type_text in {"normal", "image", "images", "图文"}:
                value = "图文"
            elif note_type_text in {"video", "视频"}:
                value = "视频"
        # Cover media is uploaded to the attachment field after the record is
        # created.  Never mirror its CDN URL into a text column: those links
        # expire and cannot be used for downstream image rewriting.
        if field_name in {"笔记封面", "封面图", "笔记图片1"}:
            value = ""
        if field_name in {"笔记tag", "话题标签"} and isinstance(value, list):
            value = ",".join(str(item) for item in value if item not in ("", None))
        if field_name == "评论图片" and isinstance(value, list):
            value = ",".join(str(item) for item in value if item not in ("", None))
        if field_name == "互动总和" and value == "":
            total = 0
            for key in ("liked_count", "like_count", "collected_count", "comment_count", "share_count", "点赞", "收藏", "评论", "分享"):
                try:
                    total += int(str(row.get(key) or 0))
                except Exception:
                    pass
            value = total
        if field_name == "互动等级" and value == "":
            total = 0
            for key in ("liked_count", "like_count", "collected_count", "comment_count", "share_count", "点赞", "收藏", "评论", "分享"):
                try:
                    total += int(str(row.get(key) or 0))
                except Exception:
                    pass
            if total >= 1000:
                value = "千互动爆文"
            elif total >= 100:
                value = "百互动爆文"
            else:
                value = "普通笔记"
        if field_name in {"总互动数据（赞+藏+评，不算分享）", "笔记总互动量（点赞+收藏+评论）", "总互动（点赞+收藏+评论）"} and value == "":
            total = 0
            for key in ("liked_count", "like_count", "collected_count", "comment_count", "点赞", "收藏", "评论"):
                try:
                    total += int(str(row.get(key) or 0))
                except Exception:
                    pass
            value = total
        if field_name == "笔记失效/正常（有失效链接作标记）" and value == "":
            value = "失效" if row.get("is_invalid") or row.get("失效") else "正常"
        if field_name in {"author_homepage_url", "账号主页", "博主主页", "主页链接"} and value == "":
            author_id = row.get("author_user_id") or row.get("user_id") or row.get("账号ID")
            value = f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else ""
        if field_name in datetime_fields and value not in ("", None):
            try:
                timestamp = int(float(str(value)))
                if timestamp > 10_000_000_000:
                    timestamp = timestamp // 1000
                value = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass
        if field_name in numeric_fields:
            # Preserve an unavailable metric as blank.  Converting missing
            # PGY values to 0 makes a failed/partial sync look like a real
            # zero (notably exposure and read counts in the monitor table).
            if value in ("", None):
                values.append("")
                continue
            try:
                text = str(value).strip()
                multiplier = 1
                if text.endswith("万"):
                    multiplier = 10000
                    text = text[:-1]
                elif text.endswith("亿"):
                    multiplier = 100000000
                    text = text[:-1]
                value = int(float(text) * multiplier)
            except Exception:
                value = ""
        values.append(value)
    return values


def _is_missing_base_cell(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        unequal = value != value
        if isinstance(unequal, bool) and unequal:
            return True
        if type(unequal).__name__ == "bool_" and bool(unequal):
            return True
    except Exception:
        pass
    return False


def _normalize_base_cell_value(value: Any, field_def: Dict[str, Any]) -> Any:
    """Convert workbook values to cell shapes accepted by lark-cli Base writes."""
    if _is_missing_base_cell(value):
        return None
    field_type = str(field_def.get("type") or "text").strip().lower()

    if field_type in {"multi_select", "multiple_select"} or field_def.get("multiple"):
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if not _is_missing_base_cell(item)]
        else:
            text = str(value).strip()
            items = [item.strip() for item in re.split(r"[,，;；]", text) if item.strip()]
        return items or None

    if field_type in {"select", "single_select"}:
        if isinstance(value, (list, tuple, set)):
            value = next((item for item in value if not _is_missing_base_cell(item)), None)
        return None if _is_missing_base_cell(value) else str(value).strip()

    if field_type in {"link", "user", "group", "group_chat"}:
        candidates = value if isinstance(value, list) else [value]
        normalized = [
            {"id": str(item.get("id"))}
            for item in candidates
            if isinstance(item, dict) and item.get("id")
        ]
        return normalized or None

    if field_type == "location":
        if isinstance(value, dict) and value.get("lng") is not None and value.get("lat") is not None:
            return {"lng": value["lng"], "lat": value["lat"]}
        return None

    if field_type in {"checkbox", "boolean", "bool"}:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "已完成"}

    if field_type == "number":
        try:
            text = str(value).strip().replace(",", "")
            multiplier = 1
            if text.endswith("万"):
                multiplier, text = 10_000, text[:-1]
            elif text.endswith("亿"):
                multiplier, text = 100_000_000, text[:-1]
            number = float(text) * multiplier
            if not math.isfinite(number):
                return None
            return int(number) if number.is_integer() else number
        except Exception:
            return None

    if field_type in {"text", "phone", "url", "email", "barcode", "datetime", "date"}:
        return str(value)

    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value if not isinstance(value, set) else sorted(value), ensure_ascii=False)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_base_row_values(
    table_fields: List[str],
    values: List[Any],
    field_defs_by_name: Dict[str, Dict[str, Any]],
) -> List[Any]:
    return [
        _normalize_base_cell_value(value, field_defs_by_name.get(field_name) or {"type": "text"})
        for field_name, value in zip(table_fields, values)
    ]


def _base_record_field_map(table_fields: List[str], values: List[Any]) -> Dict[str, Any]:
    """Build the top-level field map required by lark-cli base +record-upsert."""
    return dict(zip(table_fields, values))


def _first_media_url(value: Any) -> str:
    if value in ("", None):
        return ""
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                url = item.get("url_default") or item.get("url") or item.get("cover") or item.get("cover_url")
                if url:
                    return str(url).split(",", 1)[0].strip()
            elif item not in ("", None):
                return str(item).split(",", 1)[0].strip()
        return ""
    if isinstance(value, dict):
        url = value.get("url_default") or value.get("url") or value.get("cover") or value.get("cover_url")
        return str(url).split(",", 1)[0].strip() if url else ""
    text = str(value).strip()
    if not text:
        return ""
    with contextlib.suppress(Exception):
        parsed = json.loads(text)
        parsed_url = _first_media_url(parsed)
        if parsed_url:
            return parsed_url
    return text.split(",", 1)[0].strip()


def _cover_url_from_row(row: Dict[str, Any]) -> str:
    for key in ("cover", "cover_url", "笔记封面", "封面图", "image_list", "images", "img_urls"):
        url = _first_media_url(row.get(key))
        if url:
            return url
    return ""


def _local_cover_files_from_row(row: Dict[str, Any]) -> List[Path]:
    note_id = str(row.get("note_id") or row.get("笔记ID") or row.get("id") or "").strip()
    if not note_id:
        return []
    project_root = Path(__file__).resolve().parents[2]
    roots = [
        data_dir() / "xhs" / "images" / note_id,
        project_root / "data" / "xhs" / "images" / note_id,
        data_dir() / "xhs" / "videos" / note_id,
        project_root / "data" / "xhs" / "videos" / note_id,
    ]
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        candidates = sorted(
            (path for path in root.iterdir() if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"}),
            key=lambda path: (not path.stem.isdigit(), int(path.stem) if path.stem.isdigit() else path.name),
        )
        if candidates:
            return candidates
    return []


def _local_cover_file_from_row(row: Dict[str, Any]) -> Path | None:
    """Backward-compatible helper for callers that only need the first cover."""
    files = _local_cover_files_from_row(row)
    return files[0] if files else None


def _suffix_for_download(url: str, content_type: str) -> str:
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    by_type = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
    }
    if content_type in by_type:
        return by_type[content_type]
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


async def _download_media_to_temp_file(url: str, prefix: str = "cover") -> Path:
    if not url:
        raise ValueError("empty media url")

    def _download() -> Path:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.xiaohongshu.com/",
            },
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            content = resp.read(30 * 1024 * 1024)
            content_type = resp.headers.get("Content-Type", "")
        suffix = _suffix_for_download(url, content_type)
        target = temp_dir() / f"{prefix}_{uuid4().hex}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    return await asyncio.to_thread(_download)


async def _upload_cover_file_if_available(
    base_token: str,
    table_id: str,
    record_id: str,
    row: Dict[str, Any],
    attachment_field_ids: Dict[str, str],
) -> tuple[bool, str]:
    cover_field_id = (
        attachment_field_ids.get("封面附件")
        or attachment_field_ids.get("封面文件")
        or attachment_field_ids.get("笔记封面", "")
    )
    if not cover_field_id:
        return False, ""
    local_covers = _local_cover_files_from_row(row)
    if not local_covers:
        return False, "未找到本地封面文件，已跳过 URL 写入"
    try:
        await _upload_base_attachments(base_token, table_id, record_id, cover_field_id, local_covers)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:300]


def _chunk_table_rows(rows: List[List[Any]], chunk_size: int = 50) -> List[List[List[Any]]]:
    if not rows:
        return []
    if chunk_size <= 0:
        return [rows]
    return [rows[i:i + chunk_size] for i in range(0, len(rows), chunk_size)]


def _base_dedupe_field_candidates(data_type: str) -> List[str]:
    if data_type == "notes":
        return ["note_id", "笔记ID", "id", "发布笔记链接", "笔记链接"]
    return ["comment_id", "评论ID", "笔记链接", "评论内容"]


def _pgy_summary_path_from_request(request: PgyKolSyncRequest) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    if request.summary_path:
        path = Path(request.summary_path)
    elif request.output_dir:
        path = Path(request.output_dir) / "summary.json"
    else:
        raise HTTPException(status_code=400, detail="缺少 output_dir 或 summary_path")
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到蒲公英结果文件: {path}")
    return path


def _pick_number(*values: Any) -> Any:
    for value in values:
        if value not in ("", None):
            return value
    return ""


def _pgy_row_to_values(
    row: Dict[str, Any],
    table_fields: List[str],
    field_types: Dict[str, str] | None = None,
) -> List[Any]:
    alias_map = {
        "类型": ["row_type", "类型"],
        "目标/推荐博主": ["row_type", "类型"],
        "达人类型": ["row_type", "类型"],
        "去重键": ["dedupe_key", "去重键"],
        "排名": ["rank", "排名"],
        "推荐排名": ["rank", "排名"],
        "达人昵称": ["nickname", "博主昵称", "博主名"],
        "博主昵称": ["nickname", "达人昵称", "博主名"],
        "博主名": ["nickname", "达人昵称", "博主昵称"],
        "小红书号": ["red_id", "小红书号"],
        "小红书ID": ["red_id", "小红书号"],
        "博主主页": ["blogger_homepage_url", "博主主页"],
        "达人主页": ["blogger_homepage_url", "博主主页"],
        "主页链接": ["blogger_homepage_url", "博主主页"],
        "蒲公英主页链接": ["pgy_homepage_url", "蒲公英主页链接"],
        "目标达人昵称": ["target_nickname", "目标达人昵称"],
        "目标小红书号": ["target_red_id", "目标小红书号"],
        "地区": ["location", "地区"],
        "粉丝数": ["fans_count", "粉丝数"],
        "获赞收藏": ["like_collect_count", "获赞收藏"],
        "商业笔记数": ["business_note_count", "商业笔记数"],
        "最低报价": ["lower_price", "最低报价"],
        "图文报价": ["picture_price", "图文报价"],
        "视频报价": ["video_price", "视频报价"],
        "标签": ["tags", "标签"],
        "内容类目（标签）": ["content_category", "tags", "标签", "内容类目（标签）"],
        "合作行业": ["cooperation_industry", "合作行业"],
        "博主优势": ["kol_advantage", "博主优势"],
        "数据日期": ["data_date", "数据日期"],
        "采集博主数据日期": ["data_date", "数据日期"],
        "发布笔记数": ["note_number", "发布笔记数"],
        "曝光量": ["exposure_count", "imp", "曝光量"],
        "阅读量": ["read_count", "read", "阅读量"],
        "曝光中位数": ["imp_median", "exposure_count", "imp", "曝光中位数"],
        "日常笔记曝光中位数": ["daily_imp_median", "imp_median", "exposure_count", "imp", "曝光中位数"],
        "合作笔记曝光中位数": ["business_imp_median", "合作笔记曝光中位数"],
        "阅读中位数": ["read_median", "read_count", "read", "阅读中位数"],
        "日常笔记阅读中位数": ["daily_read_median", "read_median", "read_count", "read", "阅读中位数"],
        "合作笔记阅读中位数": ["business_read_median", "合作笔记阅读中位数"],
        "互动中位数": ["interaction_median", "互动中位数"],
        "日常笔记互动中位数": ["daily_interaction_median", "interaction_median", "互动中位数"],
        "合作笔记互动中位数": ["business_interaction_median", "合作笔记互动中位数"],
        "中位点赞量": ["like_median", "中位点赞量"],
        "日常笔记中位点赞量": ["daily_like_median", "like_median", "中位点赞量"],
        "合作笔记中位点赞量": ["business_like_median", "合作笔记中位点赞量"],
        "中位收藏量": ["collect_median", "中位收藏量"],
        "日常笔记中位收藏量": ["daily_collect_median", "collect_median", "中位收藏量"],
        "合作笔记中位收藏量": ["business_collect_median", "合作笔记中位收藏量"],
        "中位评论量": ["comment_median", "中位评论量"],
        "日常笔记中位评论量": ["daily_comment_median", "comment_median", "中位评论量"],
        "合作笔记中位评论量": ["business_comment_median", "合作笔记中位评论量"],
        "中位分享量": ["share_median", "中位分享量"],
        "日常笔记中位分享量": ["daily_share_median", "share_median", "中位分享量"],
        "合作笔记中位分享量": ["business_share_median", "合作笔记中位分享量"],
        "中位关注量": ["follow_median", "中位关注量"],
        "日常笔记中位关注量": ["daily_follow_median", "follow_median", "中位关注量"],
        "合作笔记中位关注量": ["business_follow_median", "合作笔记中位关注量"],
        "互动率": ["interaction_rate", "互动率"],
        "日常笔记互动率": ["daily_interaction_rate", "interaction_rate", "互动率"],
        "合作笔记互动率": ["business_interaction_rate", "合作笔记互动率"],
        "视频完播率": ["video_full_view_rate", "视频完播率"],
        "日常笔记视频完播率": ["daily_video_full_view_rate", "video_full_view_rate", "视频完播率"],
        "合作笔记视频完播率": ["business_video_full_view_rate", "合作笔记视频完播率"],
        "图文3秒阅读率": ["picture_3s_view_rate", "图文3秒阅读率"],
        "日常笔记图文3秒阅读率": ["daily_picture_3s_view_rate", "picture_3s_view_rate", "图文3秒阅读率"],
        "合作笔记图文3秒阅读率": ["business_picture_3s_view_rate", "合作笔记图文3秒阅读率"],
        "千赞笔记比例": ["thousand_like_percent", "千赞笔记比例"],
        "百赞笔记比例": ["hundred_like_percent", "百赞笔记比例"],
        "近7日活跃天数": ["active_day_7", "近7日活跃天数"],
        "邀约数": ["invite_num", "邀约数"],
        "响应率": ["response_rate", "响应率"],
        "粉丝增量": ["fans_increase", "粉丝增量"],
        "粉丝增长率": ["fans_growth_rate", "粉丝增长率"],
        "活跃粉丝占比": ["active_fans_rate", "活跃粉丝占比"],
        "阅读粉丝占比": ["read_fans_rate", "阅读粉丝占比"],
        "互动粉丝占比": ["engage_fans_rate", "互动粉丝占比"],
        "付费粉丝占比": ["pay_fans_rate", "付费粉丝占比"],
        "女性粉丝占比": ["female_fans_rate", "女性粉丝占比"],
        "男性粉丝占比": ["male_fans_rate", "男性粉丝占比"],
        "主要年龄段": ["main_age", "主要年龄段"],
        "省份TOP5": ["top_provinces", "省份TOP5"],
        "城市TOP5": ["top_cities", "城市TOP5"],
        "兴趣TOP8": ["top_interests", "兴趣TOP8"],
        "输出目录": ["output_dir", "输出目录"],
        "截图": ["screenshot", "截图"],
        "截图附件": ["screenshot", "截图附件"],
        "详情文本": ["detail_text", "详情文本"],
        "详情文本附件": ["detail_text", "详情文本附件"],
        "更新时间": ["updated_at", "更新时间"],
        "最新笔记更新时间": ["updated_at", "更新时间"],
        "采集时间": ["updated_at", "采集时间"],
    }
    numeric_fields = {
        "排名", "推荐排名", "粉丝数", "获赞收藏", "商业笔记数", "最低报价", "图文报价", "视频报价", "发布笔记数",
        "曝光量", "阅读量", "曝光中位数", "阅读中位数", "互动中位数", "中位点赞量", "中位收藏量", "中位评论量",
        "中位分享量", "中位关注量", "日常笔记曝光中位数", "日常笔记阅读中位数", "日常笔记互动中位数",
        "日常笔记中位点赞量", "日常笔记中位收藏量", "日常笔记中位评论量", "日常笔记中位分享量",
        "日常笔记中位关注量", "合作笔记曝光中位数", "合作笔记阅读中位数", "合作笔记互动中位数",
        "合作笔记中位点赞量", "合作笔记中位收藏量", "合作笔记中位评论量", "合作笔记中位分享量",
        "合作笔记中位关注量", "近7日活跃天数", "邀约数", "粉丝增量",
    }
    values: List[Any] = []
    detail_checked = row.get("row_type") == "目标达人" or row.get("detail_fetched") is True
    resolved_field_types = field_types or {}
    for field_name in table_fields:
        keys = alias_map.get(field_name, [field_name])
        value = ""
        for key in keys:
            if key in row and row[key] not in ("", None):
                value = row[key]
                break
        field_type = resolved_field_types.get(field_name)
        if (
            value in ("", None)
            and detail_checked
            and field_name in PGY_PLATFORM_NO_DATA_FIELDS
            and field_type == "text"
        ):
            value = PGY_PLATFORM_NO_DATA
        elif field_type == "text" and value not in ("", None):
            value = str(value)
        elif field_name in numeric_fields and value not in ("", None):
            try:
                value = float(str(value).replace(",", ""))
                if value.is_integer():
                    value = int(value)
            except Exception:
                pass
        values.append(value)
    return values


def _pgy_key_text(value: Any) -> str:
    if isinstance(value, list):
        return _pgy_key_text(value[0]) if value else ""
    if isinstance(value, dict):
        return _pgy_key_text(value.get("name") or value.get("text") or value.get("value"))
    return str(value or "").strip()


def _pgy_dedupe_key(row: Dict[str, Any]) -> str:
    row_type = _pgy_key_text(row.get("row_type") or row.get("目标/推荐博主") or row.get("类型")) or "未知"
    target = _pgy_key_text(
        row.get("target_nickname")
        or row.get("目标达人昵称")
        or row.get("target_red_id")
        or row.get("目标小红书号")
    )
    identity = _pgy_key_text(
        row.get("red_id")
        or row.get("小红书号")
        or row.get("nickname")
        or row.get("达人昵称")
    )
    return " :: ".join([row_type, target, identity]).lower()


def _pgy_row_to_record(
    row: Dict[str, Any],
    table_fields: List[str],
    field_types: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    values = _pgy_row_to_values(row, table_fields, field_types)
    return {
        field_name: value
        for field_name, value in zip(table_fields, values)
        if value not in ("", None)
    }


async def _read_existing_pgy_records(base_token: str, table_id: str, field_names: List[str]) -> Dict[str, List[str]]:
    lark_cli_bin = _find_lark_cli()
    existing: Dict[str, List[str]] = {}
    offset = 0
    limit = 200
    requested_fields = [name for name in field_names if name]
    while True:
        cmd = [
            lark_cli_bin,
            "base",
            "+record-list",
            "--as",
            "user",
            "--format",
            "json",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--offset",
            str(offset),
            "--limit",
            str(limit),
        ]
        for field_name in requested_fields:
            cmd.extend(["--field-id", field_name])
        payload = await _run_lark_cli(cmd, timeout_sec=60)
        data = payload.get("data") or {}
        fields = data.get("fields") or requested_fields
        rows = data.get("data") or []
        record_ids = data.get("record_id_list") or []
        for record_id, values in zip(record_ids, rows):
            if not isinstance(values, list):
                continue
            existing_row = {field_name: value for field_name, value in zip(fields, values)}
            key = str(existing_row.get("去重键") or "").strip().lower() or _pgy_dedupe_key({
                "row_type": existing_row.get("目标/推荐博主") or existing_row.get("类型"),
                "target_red_id": existing_row.get("目标小红书号"),
                "target_nickname": existing_row.get("目标达人昵称"),
                "red_id": existing_row.get("小红书号"),
                "nickname": existing_row.get("达人昵称"),
            })
            if key:
                existing.setdefault(key, []).append(str(record_id))
        if not data.get("has_more"):
            break
        offset += limit
    return existing


async def _read_existing_base_records(base_token: str, table_id: str, dedupe_fields: str | List[str] = "note_id") -> Dict[str, str]:
    lark_cli_bin = _find_lark_cli()
    existing: Dict[str, str] = {}
    candidate_fields = [dedupe_fields] if isinstance(dedupe_fields, str) else list(dedupe_fields)
    offset = 0
    limit = 200
    while True:
        cmd = [
            lark_cli_bin, "base", "+record-list", "--as", "user", "--format", "json",
            "--base-token", base_token, "--table-id", table_id,
            "--offset", str(offset), "--limit", str(limit),
        ]
        payload = await _run_lark_cli(cmd, timeout_sec=60)
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        rows = data.get("data") or []
        record_ids = data.get("record_id_list") or []
        for record_id, values in zip(record_ids, rows):
            if not isinstance(values, list):
                continue
            existing_row = {field_name: str(value).strip() for field_name, value in zip(fields, values) if value}
            key = next((existing_row.get(field_name, "") for field_name in candidate_fields if existing_row.get(field_name, "")), "")
            if key and key not in existing:
                existing[key] = str(record_id)
        if not data.get("has_more"):
            break
        offset += limit
    return existing


def _pgy_summary_to_rows(summary: Dict[str, Any], output_dir: str) -> List[Dict[str, Any]]:
    target = summary.get("blogger_detail") or {}
    propagation = summary.get("propagation_performance") or {}
    notes_rate = propagation.get("notes_rate") or {}
    data_summary = propagation.get("data_summary") or {}
    core_data = propagation.get("core_data") or {}
    core_sum = core_data.get("sumData") or {}
    fans = summary.get("fan_analysis") or {}
    fans_summary = fans.get("fans_summary") or {}
    target_metrics = summary.get("target_metrics") or {}
    if not target_metrics:
        fans_profile = fans.get("fans_profile") or {}
        gender = fans_profile.get("gender") or {}

        def top_percent(items: List[Dict[str, Any]], limit: int = 5) -> str:
            parts: List[str] = []
            for item in (items or [])[:limit]:
                name = item.get("name") or item.get("group") or ""
                percent = item.get("percent")
                if not name:
                    continue
                try:
                    percent_text = f"{float(percent) * 100:.1f}%" if float(percent) <= 1 else f"{float(percent):.1f}%"
                    parts.append(f"{name} {percent_text}")
                except Exception:
                    parts.append(str(name))
            return "，".join(parts)

        def dominant(items: List[Dict[str, Any]]) -> str:
            if not items:
                return ""
            best = sorted(items, key=lambda item: item.get("percent") or 0, reverse=True)[0]
            return str(best.get("name") or best.get("group") or "")

        target_metrics = {
            "blogger_homepage_url": f"https://www.xiaohongshu.com/user/profile/{target.get('userId')}" if target.get("userId") else "",
            "kol_advantage": data_summary.get("kolAdvantage") or "",
            "data_date": data_summary.get("dateKey") or core_sum.get("dateKey") or fans_profile.get("dateKey") or "",
            "note_number": data_summary.get("noteNumber") or notes_rate.get("noteNumber") or "",
            "exposure_count": core_sum.get("imp") or data_summary.get("imp") or "",
            "read_count": core_sum.get("read") or data_summary.get("read") or "",
            "imp_median": notes_rate.get("impMedian") or data_summary.get("mAccumImpNum") or "",
            "read_median": notes_rate.get("readMedian") or data_summary.get("readMedian") or "",
            "interaction_median": notes_rate.get("interactionMedian") or data_summary.get("interactionMedian") or "",
            "like_median": notes_rate.get("likeMedian") or "",
            "collect_median": notes_rate.get("collectMedian") or "",
            "comment_median": notes_rate.get("commentMedian") or "",
            "share_median": notes_rate.get("shareMedian") or "",
            "follow_median": notes_rate.get("mFollowCnt") or notes_rate.get("mfollowCnt") or "",
            "interaction_rate": notes_rate.get("interactionRate") or "",
            "video_full_view_rate": notes_rate.get("videoFullViewRate") or "",
            "picture_3s_view_rate": notes_rate.get("picture3sViewRate") or "",
            "thousand_like_percent": notes_rate.get("thousandLikePercent") or "",
            "hundred_like_percent": notes_rate.get("hundredLikePercent") or "",
            "active_day_7": data_summary.get("activeDayInLast7") or "",
            "invite_num": data_summary.get("inviteNum") or "",
            "response_rate": data_summary.get("responseRate") or "",
            "fans_num": fans_summary.get("fansNum") or target.get("fansCount") or "",
            "fans_increase": fans_summary.get("fansIncreaseNum") or "",
            "fans_growth_rate": fans_summary.get("fansGrowthRate") or data_summary.get("fans30GrowthRate") or "",
            "active_fans_rate": fans_summary.get("activeFansRate") or "",
            "read_fans_rate": fans_summary.get("readFansRate") or "",
            "engage_fans_rate": fans_summary.get("engageFansRate") or "",
            "pay_fans_rate": fans_summary.get("payFansUserRate30d") or "",
            "female_fans_rate": gender.get("female") or "",
            "male_fans_rate": gender.get("male") or "",
            "main_age": dominant(fans_profile.get("ages") or []),
            "top_provinces": top_percent(fans_profile.get("provinces") or [], limit=5),
            "top_cities": top_percent(fans_profile.get("cities") or [], limit=5),
            "top_interests": top_percent(fans_profile.get("interests") or [], limit=8),
        }
    else:
        fallback_metrics = {
            "blogger_homepage_url": f"https://www.xiaohongshu.com/user/profile/{target.get('userId')}" if target.get("userId") else "",
            "data_date": data_summary.get("dateKey") or core_sum.get("dateKey") or "",
            "exposure_count": core_sum.get("imp") or data_summary.get("imp") or "",
            "read_count": core_sum.get("read") or data_summary.get("read") or "",
        }
        for key, value in fallback_metrics.items():
            if target_metrics.get(key) in ("", None) and value not in ("", None):
                target_metrics[key] = value
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_tags = []
    for tag in target.get("featureTags") or target.get("contentTags") or []:
        if isinstance(tag, str):
            target_tags.append(tag)
        elif isinstance(tag, dict):
            target_tags.append(str(tag.get("name") or tag.get("tagName") or tag.get("taxonomy1Tag") or tag))
        else:
            target_tags.append(str(tag))
    target_row = {
        "row_type": "目标达人",
        "rank": 0,
        "nickname": summary.get("nickname") or target.get("name") or "",
        "red_id": summary.get("red_id") or target.get("redId") or "",
        "blogger_homepage_url": target_metrics.get("blogger_homepage_url") or (f"https://www.xiaohongshu.com/user/profile/{target.get('userId')}" if target.get("userId") else ""),
        "pgy_homepage_url": f"https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/{target.get('userId')}" if target.get("userId") else "",
        "target_nickname": summary.get("nickname") or target.get("name") or "",
        "target_red_id": summary.get("red_id") or target.get("redId") or "",
        "location": target.get("location") or "",
        "fans_count": target.get("fansCount") or "",
        "like_collect_count": target.get("likeCollectCountInfo") or "",
        "business_note_count": target.get("businessNoteCount") or "",
        "lower_price": target.get("lowerPrice") or "",
        "picture_price": target.get("picturePrice") or "",
        "video_price": target.get("videoPrice") or "",
        "tags": ",".join(target_tags),
        **target_metrics,
        "output_dir": output_dir,
        "screenshot": summary.get("screenshot") or "",
        "detail_text": summary.get("detail_text") or "",
        "updated_at": updated_at,
    }
    target_row["dedupe_key"] = _pgy_dedupe_key(target_row)
    rows = [target_row]
    for item in summary.get("similar_creators") or []:
        if not isinstance(item, dict):
            continue
        detail_fetched = item.get("detail_fetched")
        if detail_fetched is None:
            detail_fetched = bool(item.get("detail_text") or item.get("screenshot"))
        if not detail_fetched:
            # The first search only returns recommendation-card data. Do not
            # write incomplete similar-creator rows until their detail fetch
            # has completed through the explicit selection action.
            continue
        row = {
            "row_type": "相似博主",
            "target_nickname": target_row["nickname"],
            "target_red_id": target_row["red_id"],
            "pgy_homepage_url": f"https://pgy.xiaohongshu.com/solar/pre-trade/blogger-detail/{item.get('userId')}" if item.get("userId") else "",
            "output_dir": output_dir,
            "screenshot": item.get("screenshot") or "",
            "detail_text": item.get("detail_text") or "",
            "updated_at": updated_at,
            **item,
        }
        row["dedupe_key"] = _pgy_dedupe_key(row)
        rows.append(row)
    return rows


async def _upload_base_attachments(
    base_token: str,
    table_id: str,
    record_id: str,
    field_id: str,
    file_paths: List[Path],
) -> None:
    if not file_paths:
        return
    project_root = Path(__file__).resolve().parents[2]
    staged_paths: List[Path] = []
    cli_paths: List[Path] = []
    try:
        for file_path in file_paths:
            path = Path(file_path)
            if not path.is_absolute():
                path = project_root / path
            path = path.resolve()
            if not path.exists() or not path.is_file():
                continue
            try:
                cli_path = path.relative_to(project_root)
            except ValueError:
                staging_dir = project_root / ".tmp_lark_uploads"
                staging_dir.mkdir(parents=True, exist_ok=True)
                staged_path = staging_dir / f"{uuid4().hex}_{path.name}"
                shutil.copy2(path, staged_path)
                staged_paths.append(staged_path)
                cli_path = staged_path.relative_to(project_root)
            cli_paths.append(cli_path)
        if not cli_paths:
            return
        command = [
            _find_lark_cli(),
            "base", "+record-upload-attachment",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--record-id", record_id,
            "--field-id", field_id,
        ]
        for cli_path in cli_paths:
            command.extend(["--file", cli_path.as_posix()])
        await _run_lark_cli(command, timeout_sec=60)
    finally:
        for staged_path in staged_paths:
            with contextlib.suppress(Exception):
                staged_path.unlink()
        staging_dir = project_root / ".tmp_lark_uploads"
        with contextlib.suppress(Exception):
            staging_dir.rmdir()


async def _upload_base_attachment(base_token: str, table_id: str, record_id: str, field_id: str, file_path: str) -> None:
    if file_path:
        await _upload_base_attachments(base_token, table_id, record_id, field_id, [Path(file_path)])


async def _sync_pgy_summary_to_base(request: PgyKolSyncRequest) -> Dict[str, Any]:
    if not request.base_token or not request.table_id:
        raise HTTPException(status_code=400, detail="缺少 base_token 或 table_id")
    summary_path = _pgy_summary_path_from_request(request)
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取蒲公英 summary.json 失败: {exc}") from exc

    field_defs = await _ensure_creator_selection_fields(request.base_token, request.table_id)
    if not field_defs:
        raise HTTPException(status_code=400, detail="目标表没有字段，无法同步")
    table_fields = [
        field.get("name")
        for field in field_defs
        if field.get("name") and field.get("type") not in {
            "attachment", "formula", "lookup", "auto_number", "not_support",
            "created_at", "updated_at", "created_by", "updated_by",
            "created_time", "modified_time", "created_user", "modified_user",
        }
    ]
    field_types = {field.get("name"): field.get("type") for field in field_defs}
    writable_fields = [name for name in table_fields if field_types.get(name) != "attachment"]
    output_dir = str(summary_path.parent)
    rows = _pgy_summary_to_rows(summary, output_dir)
    dedupe_fields = [name for name in ["去重键", "目标/推荐博主", "类型", "达人昵称", "小红书号", "目标达人昵称", "目标小红书号"] if name in table_fields]
    existing_records = await _read_existing_pgy_records(request.base_token, request.table_id, dedupe_fields)
    lark_cli_bin = _find_lark_cli()
    created = 0
    updated = 0
    skipped = 0
    record_ids: List[str] = []
    rows_for_attachments: List[Dict[str, Any]] = []
    new_rows: List[Dict[str, Any]] = []
    seen_new_keys: set[str] = set()
    for row in rows:
        key = str(row.get("dedupe_key") or _pgy_dedupe_key(row)).lower()
        if key in seen_new_keys:
            skipped += 1
            continue
        existing_record_ids = existing_records.get(key) or []
        if existing_record_ids:
            payload = _pgy_row_to_record(row, writable_fields, field_types)
            for record_id in existing_record_ids:
                with _lark_json_arg(payload) as json_arg:
                    await _run_lark_cli(
                        [
                            lark_cli_bin,
                            "base",
                            "+record-upsert",
                            "--as",
                            "user",
                            "--base-token",
                            request.base_token,
                            "--table-id",
                            request.table_id,
                            "--record-id",
                            record_id,
                            "--json",
                            json_arg,
                        ],
                        timeout_sec=60,
                    )
            updated += len(existing_record_ids)
        else:
            new_rows.append(row)
            seen_new_keys.add(key)

    table_rows = [_pgy_row_to_values(row, writable_fields, field_types) for row in new_rows]
    for i in range(0, len(table_rows), 200):
        payload = {"fields": writable_fields, "rows": table_rows[i:i + 200]}
        with _lark_json_arg(payload) as json_arg:
            created_payload = await _run_lark_cli(
                [
                    lark_cli_bin,
                    "base",
                    "+record-batch-create",
                    "--as",
                    "user",
                    "--base-token",
                    request.base_token,
                    "--table-id",
                    request.table_id,
                    "--json",
                    json_arg,
                ],
                timeout_sec=60,
            )
        batch_ids = (created_payload.get("data") or {}).get("record_id_list") or []
        record_ids.extend([str(record_id) for record_id in batch_ids if record_id])
        batch_rows = new_rows[i:i + 200]
        rows_for_attachments.extend(batch_rows)
        created += len(batch_rows)
    attachment_field_ids = {
        str(field.get("name")): str(field.get("id"))
        for field in field_defs
        if field.get("type") == "attachment" and field.get("name") and field.get("id")
    }
    screenshot_field = "截图" if "截图" in attachment_field_ids else "截图附件"
    attachment_uploads = 0
    attachment_errors: List[str] = []
    for record_id, row in zip(record_ids, rows_for_attachments):
        if screenshot_field in attachment_field_ids and row.get("screenshot"):
            try:
                await _upload_base_attachment(
                    request.base_token,
                    request.table_id,
                    record_id,
                    attachment_field_ids[screenshot_field],
                    row.get("screenshot") or "",
                )
                attachment_uploads += 1
            except Exception as exc:
                attachment_errors.append(str(exc)[:300])
    return {
        "status": "success",
        "created": created,
        "updated": updated,
        "skipped_duplicates": skipped,
        "attachment_uploads": attachment_uploads,
        "attachment_errors": attachment_errors,
        "base_token": request.base_token,
        "table_id": request.table_id,
        "summary": str(summary_path),
        "fields": table_fields,
        "target_url": f"https://my.feishu.cn/base/{request.base_token}?table={request.table_id}",
    }


async def _read_rule_table(base_token: str, table_id: str) -> List[Dict]:
    if not base_token or not table_id:
        raise HTTPException(status_code=400, detail="缺少 base_token 或 table_id")
    lark_cli_bin = _find_lark_cli()
    cmd = [lark_cli_bin, "base", "+record-list", "--as", "user", "--format", "json", "--base-token", base_token, "--table-id", table_id, "--limit", "200"]
    result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20, check=False)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f"读取规则表失败: {(result.stderr or result.stdout)[:400]}")
    payload = json.loads(result.stdout)
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=f"规则表返回失败: {payload}")
    data = payload.get("data", {})
    fields = data.get("fields", [])
    rows = data.get("data", [])
    rules: List[Dict] = []
    for row in rows:
        if isinstance(row, list):
            rules.append({fields[i]: row[i] for i in range(min(len(fields), len(row)))})
    return rules


async def _wait_crawler_idle(timeout_sec: int = 1800) -> bool:
    for _ in range(timeout_sec):
        if crawler_manager.get_status().get("status") == "idle":
            return True
        await asyncio.sleep(1)
    return False


async def _refresh_collab_creator_notes(request: CollaborationMonitorStartRequest) -> None:
    raw_note_links = [
        item.strip()
        for item in re.split(r"[\n\r,，;；]+", request.note_links or "")
        if item.strip()
    ]
    creator_id_list = _split_creator_inputs(request.creator_ids)
    if not raw_note_links and not creator_id_list:
        return
    if crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(status_code=400, detail="当前有任务正在运行，无法启动合作监控抓取")
    if raw_note_links:
        note_urls_by_id: Dict[str, str] = {}
        errors: List[str] = []
        for link in raw_note_links:
            try:
                normalized_url = await asyncio.to_thread(_normalized_note_url_from_link, link)
                note_id = _note_id_from_link(normalized_url, resolve_short_link=False)
                note_urls_by_id.setdefault(note_id, normalized_url)
            except ValueError as exc:
                errors.append(f"{link[:80]}：{exc}")
        if errors:
            raise HTTPException(
                status_code=400,
                detail="合作笔记链接格式不正确，请填写小红书笔记长链或短链：" + "；".join(errors[:5]),
            )
        note_urls = list(note_urls_by_id.values())
        start_request = CrawlerStartRequest(
            platform="xhs", login_type=request.login_type, crawler_type="detail",
            specified_ids=",".join(note_urls), max_notes_count=max(1, len(note_urls)),
            max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
            enable_comments=request.enable_comments, enable_sub_comments=request.enable_sub_comments,
            enable_media=request.enable_media, save_option=request.save_option, cookies=request.cookies, headless=request.headless,
        )
        _clear_mode_data_files("detail")
    else:
        start_request = CrawlerStartRequest(
            platform="xhs", login_type=request.login_type, crawler_type="creator",
            creator_ids=",".join(creator_id_list), max_notes_count=max(1, request.notes_per_creator),
            max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
            enable_comments=request.enable_comments, enable_sub_comments=request.enable_sub_comments,
            enable_media=request.enable_media, save_option=request.save_option, cookies=request.cookies, headless=request.headless,
        )
        _clear_creator_data_files()
    success = await crawler_manager.start(start_request)
    if not success:
        raise HTTPException(status_code=500, detail="合作监控抓取任务启动失败")
    if not await _wait_crawler_idle(timeout_sec=1800):
        raise HTTPException(status_code=504, detail="合作监控抓取超时（30分钟）")
    if crawler_manager.process and crawler_manager.process.returncode not in (None, 0):
        failure_messages = [
            str(entry.message or "").strip()
            for entry in crawler_manager.logs[-30:]
            if entry.level in {"error", "warning"} and str(entry.message or "").strip()
        ]
        failure_detail = failure_messages[-1] if failure_messages else "小红书未返回笔记详情"
        raise HTTPException(status_code=502, detail=f"合作笔记抓取失败：{failure_detail}")


def _clear_mode_data_files(mode: str) -> None:
    data_roots = [data_dir() / "xhs"]
    project_root = Path(__file__).resolve().parents[2]
    legacy_data_root = project_root / "data" / "xhs"
    if legacy_data_root.resolve() != data_roots[0].resolve():
        data_roots.append(legacy_data_root)
    for data_root in data_roots:
        for suffix_dir in ("csv", "jsonl", "json", "excel"):
            dir_path = data_root / suffix_dir
            if not dir_path.exists():
                continue
            for suffix in ("contents", "comments"):
                for f in dir_path.glob(f"{mode}_{suffix}_*.*"):
                    with contextlib.suppress(Exception):
                        f.unlink()
        # ExcelStoreBase writes a whole workbook directly under data/xhs.
        for pattern in (f"xhs_{mode}_*.xlsx", f"xhs_{mode}_*.xls"):
            for f in data_root.glob(pattern):
                with contextlib.suppress(Exception):
                    f.unlink()


def _clear_creator_data_files() -> None:
    _clear_mode_data_files("creator")


def _merge_pgy_note_data(row: Dict[str, Any], pgy_note: Dict[str, Any] | None) -> Dict[str, Any]:
    """Let PGY's single-note report override XHS values it actually provides."""
    merged = dict(row)
    if not pgy_note:
        return merged
    override_fields = {
        "note_url",
        "title",
        "author_nickname",
        "author_homepage_url",
        "publish_date",
        "tag_list",
        "liked_count",
        "collected_count",
        "comment_count",
        "share_count",
        "exposure_count",
        "read_count",
    }
    for key in override_fields:
        value = pgy_note.get(key)
        if value not in (None, "", []):
            merged[key] = value
    for key, value in pgy_note.items():
        if key.startswith("pgy_") and value not in (None, "", []):
            merged[key] = value
    merged["pgy_note_source"] = str(pgy_note.get("pgy_note_source") or "蒲公英笔记报告")
    return merged


async def _fetch_pgy_note_data(note_ids: List[str]) -> Dict[str, Any]:
    unique_note_ids = list(dict.fromkeys(str(note_id).strip() for note_id in note_ids if str(note_id).strip()))
    if not unique_note_ids:
        return {"status": "skipped", "requested_count": 0, "matched_count": 0, "notes": []}
    args = ["run-note-data", "--note-ids", ",".join(unique_note_ids)]
    # Reuse the long-lived login browser when present. Without it, try the
    # saved persistent profile headlessly so a scheduled monitor never opens
    # an unexpected window.
    if not _pgy_cdp_available():
        args.append("--headless")
    result = await _run_pgy_automation(
        args,
        timeout_sec=max(180, min(1200, 45 + len(unique_note_ids) * 15)),
    )
    if _pgy_login_required(result):
        return {
            "status": "login_required",
            "requested_count": len(unique_note_ids),
            "matched_count": 0,
            "notes": [],
            "error": result.get("error") or "蒲公英登录已失效",
        }
    if result.get("status") == "error" or result.get("returncode"):
        return {
            "status": "error",
            "requested_count": len(unique_note_ids),
            "matched_count": 0,
            "notes": [],
            "error": str(result.get("error") or "蒲公英单篇笔记数据读取失败")[:500],
        }
    return result


def _pgy_note_result_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": result.get("status") or "error",
        "source": result.get("source") or "蒲公英笔记报告",
        "requested_count": int(result.get("requested_count") or 0),
        "matched_count": int(result.get("matched_count") or 0),
        "missing_count": int(result.get("missing_count") or 0),
        "missing_note_ids": list(result.get("missing_note_ids") or [])[:20],
        "error": str(result.get("error") or "")[:500],
    }


async def _sync_collaboration_snapshot(request: CollaborationMonitorStartRequest, monitor_tag: str) -> Dict[str, Any]:
    table_fields = await _read_table_fields(request.base_token, request.table_id)
    if not table_fields:
        raise HTTPException(status_code=400, detail="笔记数据监测表没有可用字段")
    crawler_mode = "detail" if request.note_links.strip() else "creator"
    file_path = Path(request.file_path) if request.file_path else _latest_local_file("notes", crawler_mode, strict_mode=True)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"本地文件不存在: {file_path}")
    all_rows = _read_local_rows(file_path)
    creator_filters = set()
    for cid in _split_creator_inputs(request.creator_ids):
        if "/user/profile/" in cid:
            creator_filters.add(cid.split("/user/profile/")[-1].split("?")[0].strip())
        else:
            cid_stripped = cid.strip()
            if not cid_stripped.startswith("__note__:"):
                creator_filters.add(cid_stripped)
    eligible_rows: List[Dict[str, Any]] = []
    for row in all_rows:
        # Creator-mode rows usually do not contain source_keyword; do not over-filter them.
        if request.source_keyword:
            row_keyword = str(row.get("source_keyword", "")).strip()
            if row_keyword and row_keyword != request.source_keyword:
                continue
        if creator_filters:
            author_id = str(row.get("author_user_id") or row.get("user_id") or "").strip()
            if not author_id or author_id not in creator_filters:
                continue
        eligible_rows.append(row)
    pgy_result = await _fetch_pgy_note_data(
        [str(row.get("note_id") or row.get("笔记ID") or row.get("id") or "").strip() for row in eligible_rows]
    )
    pgy_notes_by_id = {
        str(item.get("note_id") or "").strip(): item
        for item in (pgy_result.get("notes") or [])
        if isinstance(item, dict) and str(item.get("note_id") or "").strip()
    }
    rows: List[List[Any]] = []
    for source_row in eligible_rows:
        row = _merge_pgy_note_data(
            source_row,
            pgy_notes_by_id.get(str(source_row.get("note_id") or source_row.get("笔记ID") or source_row.get("id") or "").strip()),
        )
        row["序号"] = len(rows) + 1
        row["项目名"] = request.project_name
        row["所属项目"] = request.project_name
        row["监控周期"] = monitor_tag
        rows.append(_row_to_table_values(row, table_fields, "notes"))
    if not rows:
        raise HTTPException(status_code=400, detail="合作监控未命中可同步数据")
    lark_cli_bin = _find_lark_cli()
    existing = await _read_existing_base_records(request.base_token, request.table_id, "note_id")
    created = 0
    updated = 0
    note_id_idx = table_fields.index("note_id") if "note_id" in table_fields else -1
    for row_values in rows:
        note_id = str(row_values[note_id_idx]).strip() if note_id_idx >= 0 and row_values[note_id_idx] else ""
        record_id = existing.get(note_id) if note_id else None
        if record_id:
            payload = _base_record_field_map(table_fields, row_values)
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [lark_cli_bin, "base", "+record-upsert", "--as", "user", "--base-token", request.base_token, "--table-id", request.table_id, "--record-id", record_id, "--json", json_arg],
                    timeout_sec=60,
                )
            updated += 1
        else:
            payload = {"fields": table_fields, "rows": [row_values]}
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [lark_cli_bin, "base", "+record-batch-create", "--as", "user", "--base-token", request.base_token, "--table-id", request.table_id, "--json", json_arg],
                    timeout_sec=60,
                )
            created += 1
    return {
        "created": created,
        "updated": updated,
        "file": str(file_path),
        "pgy": _pgy_note_result_summary(pgy_result),
    }


async def _sync_collaboration_comments(request: CollaborationMonitorStartRequest, monitor_tag: str) -> Dict[str, Any]:
    if not request.comments_table_id:
        return {"created": 0, "updated": 0, "skipped": True, "reason": "comments_table_id not configured"}
    table_fields = await _read_table_fields(request.base_token, request.comments_table_id)
    if not table_fields:
        return {"created": 0, "updated": 0, "skipped": True, "reason": "comments table has no fields"}
    try:
        crawler_mode = "detail" if request.note_links.strip() else "creator"
        file_path = _latest_local_file("comments", crawler_mode, strict_mode=True)
    except HTTPException:
        return {"created": 0, "updated": 0, "skipped": True, "reason": "no local comments file found"}
    if not file_path.exists():
        return {"created": 0, "updated": 0, "skipped": True, "reason": f"comments file not found: {file_path}"}
    all_rows = _read_local_rows(file_path)
    rows_to_sync: List[Dict[str, Any]] = []
    for row in all_rows:
        row["项目名"] = request.project_name
        row["所属项目"] = request.project_name
        row["监控周期"] = monitor_tag
        rows_to_sync.append(row)
    if not rows_to_sync:
        return {"created": 0, "updated": 0, "skipped": True, "reason": "no matching comment rows"}
    lark_cli_bin = _find_lark_cli()
    existing = await _read_existing_base_records(request.base_token, request.comments_table_id, "comment_id")
    created = 0
    updated = 0
    for row in rows_to_sync:
        comment_id = str(row.get("comment_id") or "").strip()
        values = _row_to_table_values(row, table_fields, "comments")
        record_id = existing.get(comment_id) if comment_id else None
        if record_id:
            payload = _base_record_field_map(table_fields, values)
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [lark_cli_bin, "base", "+record-upsert", "--as", "user", "--base-token", request.base_token, "--table-id", request.comments_table_id, "--record-id", record_id, "--json", json_arg],
                    timeout_sec=60,
                )
            updated += 1
        else:
            payload = {"fields": table_fields, "rows": [values]}
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [lark_cli_bin, "base", "+record-batch-create", "--as", "user", "--base-token", request.base_token, "--table-id", request.comments_table_id, "--json", json_arg],
                    timeout_sec=60,
                )
            created += 1
    return {"created": created, "updated": updated, "file": str(file_path)}


async def _collaboration_job_loop(job_id: str, request: CollaborationMonitorStartRequest) -> None:
    interval_seconds = request.interval_hours * 3600
    monitor_tag = f"{request.interval_hours}h"
    while True:
        await asyncio.sleep(interval_seconds)
        job = collaboration_monitor_jobs.get(job_id)
        if not job:
            return
        job["last_run_at"] = datetime.now().isoformat()
        try:
            await _refresh_collab_creator_notes(request)
            notes_result = await _sync_collaboration_snapshot(request, monitor_tag)
            comments_result = await _sync_collaboration_comments(request, monitor_tag)
            job["last_result"] = {"notes": notes_result, "comments": comments_result}
            job["last_error"] = ""
        except Exception as e:
            job["last_error"] = str(e)


@router.get("/preflight")
async def preflight_check(keyword: str = "测试"):
    """
    Pre-crawl login state preflight check.
    Grabs cookies from CDP browser via DevTools Protocol, runs pong + keyword dry-run.
    Returns pass/fail so the frontend can block task start if login is stale.
    """
    import httpx as _httpx
    from tools import utils as t_utils
    from tools.crawler_util import get_platform_user_agent

    cdp_port = getattr(config, "CDP_DEBUG_PORT", 9222)
    cdp_base = f"http://127.0.0.1:{cdp_port}"

    try:
        # Step 1: Get cookies from Chrome via CDP HTTP API
        async with _httpx.AsyncClient() as http:
            # Get list of targets to find a xiaohongshu page
            targets_resp = await http.get(f"{cdp_base}/json", timeout=5)
            targets = targets_resp.json()

        xhs_target = None
        for t in targets:
            if "xiaohongshu.com" in (t.get("url") or ""):
                xhs_target = t
                break

        if not xhs_target:
            return {"pass": False, "error": "No xiaohongshu tab found in Chrome CDP", "detail": {"targets": [t.get("url", "")[:60] for t in targets[:5]]}}

        # Use CDP WebSocket to get cookies for .xiaohongshu.com
        import websockets
        ws_url = xhs_target.get("webSocketDebuggerUrl")
        if not ws_url:
            return {"pass": False, "error": "No WebSocket debugger URL for XHS tab", "detail": {}}

        import json as _json
        async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
            # Get all cookies for xiaohongshu.com domain
            await ws.send(_json.dumps({"id": 1, "method": "Network.getCookies", "params": {"urls": ["https://www.xiaohongshu.com", "https://edith.xiaohongshu.com"]}}))
            resp = _json.loads(await ws.recv())

        cdp_cookies = resp.get("result", {}).get("cookies", [])
        # Convert CDP cookie format to our format
        cookie_list = [{"name": c["name"], "value": c["value"]} for c in cdp_cookies]
        cookie_str, cookie_dict = t_utils.convert_cookies(cookie_list)

        if not cookie_dict.get("web_session"):
            return {
                "pass": False,
                "error": "No web_session cookie found in browser",
                "detail": {"cookie_keys": list(cookie_dict.keys())},
            }

        # Step 2: Use XHS client to do pong + search dry-run
        # We need to sign requests — import the signing function
        from media_platform.xhs.playwright_sign import sign_with_xhshow
        from media_platform.xhs.help import get_search_id
        from tools.httpx_util import make_async_client

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json;charset=UTF-8",
            "origin": "https://www.xiaohongshu.com",
            "referer": "https://www.xiaohongshu.com/",
            "user-agent": get_platform_user_agent(),
            "Cookie": cookie_str,
        }

        # Pong: check selfinfo
        pong_ok = False
        uri = "/api/sns/web/v1/user/selfinfo"
        signs = sign_with_xhshow(uri=uri, data={}, cookie_str=cookie_str, method="GET")
        pong_headers = {**headers, "X-S": signs["x-s"], "X-T": signs["x-t"], "x-S-Common": signs["x-s-common"], "X-B3-Traceid": signs["x-b3-traceid"]}
        async with make_async_client() as client:
            resp = await client.get(f"https://edith.xiaohongshu.com{uri}", headers=pong_headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data", {}).get("result", {}).get("success"):
                pong_ok = True

        if not pong_ok:
            return {"pass": False, "error": "pong failed: API login state invalid (web_session expired)", "detail": {"cookie_keys": list(cookie_dict.keys()), "pong_status": resp.status_code}}

        # Search dry-run
        search_ok = False
        search_error = None
        search_uri = "/api/sns/web/v1/search/notes"
        search_data = {"keyword": keyword, "page": 1, "page_size": 1, "search_id": get_search_id(), "sort": "general", "note_type": 0}
        signs2 = sign_with_xhshow(uri=search_uri, data=search_data, cookie_str=cookie_str, method="POST")
        search_headers = {**headers, "X-S": signs2["x-s"], "X-T": signs2["x-t"], "x-S-Common": signs2["x-s-common"], "X-B3-Traceid": signs2["x-b3-traceid"]}
        async with make_async_client() as client:
            resp2 = await client.post(f"https://edith.xiaohongshu.com{search_uri}", headers=search_headers, content=_json.dumps(search_data, separators=(",", ":"), ensure_ascii=False), timeout=10)
        if resp2.status_code == 200:
            sdata = resp2.json()
            if sdata.get("success"):
                search_ok = True
            else:
                search_error = sdata.get("msg", "unknown error")
        else:
            search_error = f"HTTP {resp2.status_code}"

        passed = pong_ok and search_ok
        return {
            "pass": passed,
            "error": search_error if not passed else None,
            "detail": {"pong_ok": pong_ok, "search_ok": search_ok, "cookie_keys": list(cookie_dict.keys())},
        }
    except Exception as e:
        import traceback
        return {"pass": False, "error": f"Preflight exception: {type(e).__name__}: {e}", "detail": {"traceback": traceback.format_exc()[-1000:]}}


@router.get("/browser-cookies")
async def browser_cookies():
    cdp_port = getattr(config, "CDP_DEBUG_PORT", 9222)
    cdp_base = f"http://127.0.0.1:{cdp_port}"
    try:
        data = await _read_xhs_cookies_from_cdp(cdp_base)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"读取浏览器 Cookie 失败: {exc}") from exc
    if not data["cookie_dict"].get("web_session"):
        raise HTTPException(
            status_code=400,
            detail="浏览器里没有有效的小红书登录态，请先在浏览器打开小红书并登录",
        )
    return {
        "status": "ok",
        "cookies": data["cookies"],
        "cookie_keys": data["cookie_keys"],
        "targets": data["targets"],
    }


@router.post("/xhs/login-browser")
async def xhs_login_browser():
    cdp_port = getattr(config, "CDP_DEBUG_PORT", 9222)
    cdp_base = f"http://127.0.0.1:{cdp_port}"
    message = "小红书登录浏览器已打开，请在浏览器内完成登录后再点击读取 Cookie"

    if _xhs_cdp_available(cdp_base):
        opened_url = _open_url_in_cdp(cdp_base, XHS_LOGIN_URL)
        browser_focused = _focus_detected_browser()
        return {
            "status": "login_window_opened",
            "message": message,
            "cdp": cdp_base,
            "url": XHS_LOGIN_URL,
            "opened_url": opened_url,
            "browser_focused": browser_focused,
        }

    launcher = BrowserLauncher()
    browser_paths = launcher.detect_browser_paths()
    if not browser_paths:
        raise HTTPException(status_code=500, detail="未找到 Chrome 或 Edge，请先安装浏览器后重试")

    user_data_dir = ""
    if getattr(config, "SAVE_LOGIN_STATE", True):
        user_data_dir = str(browser_data_dir() / f"cdp_{config.USER_DATA_DIR % config.PLATFORM}")
        os.makedirs(user_data_dir, exist_ok=True)

    try:
        launcher.launch_browser(
            browser_path=browser_paths[0],
            debug_port=cdp_port,
            headless=False,
            user_data_dir=user_data_dir or None,
            start_url=XHS_LOGIN_URL,
        )
        ready = await asyncio.to_thread(
            launcher.wait_for_browser_ready,
            cdp_port,
            getattr(config, "BROWSER_LAUNCH_TIMEOUT", 120),
        )
        if not ready:
            raise HTTPException(status_code=500, detail=f"小红书登录浏览器启动超时，请确认 {cdp_port} 端口未被占用")
        browser_focused = launcher.focus_browser_window(browser_paths[0])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"小红书登录浏览器启动失败: {exc}") from exc

    return {
        "status": "login_window_opened",
        "message": message,
        "cdp": cdp_base,
        "url": XHS_LOGIN_URL,
        "opened_url": True,
        "browser_focused": browser_focused,
    }


@router.post("/start")
async def start_crawler(request: CrawlerStartRequest):
    success = await crawler_manager.start(request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")
    return {"status": "ok", "message": "Crawler started successfully"}


@router.post("/start-sample-creators")
async def start_sample_creators(request: SampleCreatorStartRequest):
    creator_id_list = _split_creator_inputs(request.creator_ids)
    if not creator_id_list:
        raise HTTPException(status_code=400, detail="请至少提供1个样本账号链接或ID")
    start_request = CrawlerStartRequest(
        platform=request.platform, login_type=request.login_type, crawler_type="creator",
        creator_ids=",".join(creator_id_list), max_notes_count=max(1, request.notes_per_creator),
        max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
        enable_comments=request.enable_comments, enable_sub_comments=request.enable_sub_comments,
        enable_media=request.enable_media, save_option=request.save_option, cookies=request.cookies, headless=request.headless,
    )
    _clear_creator_data_files()
    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start sample creator crawler")
    return {"status": "ok", "message": "样本账号抓取任务已启动", "creator_count": len(creator_id_list), "notes_per_creator": start_request.max_notes_count}


@router.post("/account-monitor/start")
async def start_account_monitor(request: SampleCreatorStartRequest):
    """Run an account report using the requested note count and generate the Excel layout."""
    creator_links = _split_creator_inputs(request.creator_ids)
    if not creator_links:
        raise HTTPException(status_code=400, detail="请至少提供 1 个小红书账号主页链接")
    if crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(status_code=400, detail="当前已有抓取任务在运行，请等待完成后再启动账号内容监测")

    normalized_links: List[str] = []
    link_errors: List[str] = []
    for raw_link in creator_links:
        try:
            normalized_links.append(await asyncio.to_thread(_profile_url_from_link, raw_link))
        except ValueError as exc:
            link_errors.append(f"{raw_link[:80]}：{exc}")
    if link_errors:
        raise HTTPException(
            status_code=400,
            detail=(
                "主页链接格式不正确。请填写小红书账号主页链接，例如 "
                "https://www.xiaohongshu.com/user/profile/账号标识；不要填写蒲公英链接、笔记链接、昵称或小红书号。"
                "没有蒲公英主页不影响小红书主页分析，系统会在结果中标记“无蒲公英主页”。错误详情："
                + "；".join(link_errors[:5])
            ),
        )
    normalized_links = _dedupe_sample_accounts(normalized_links)
    if not normalized_links:
        raise HTTPException(status_code=400, detail="未识别到有效的小红书账号主页链接")

    requested_creator_ids = [_profile_id_from_url(link) for link in normalized_links]
    source_started_at = time.time()

    notes_per_creator = 10
    start_request = CrawlerStartRequest(
        platform=request.platform,
        login_type=request.login_type,
        crawler_type="creator",
        creator_ids=",".join(normalized_links),
        max_notes_count=notes_per_creator,
        max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
        enable_comments=False,
        enable_sub_comments=False,
        enable_media=request.enable_media,
        # The report builder consumes a fresh local file. Keep this workflow on
        # CSV regardless of the global workbench save preference.
        save_option="csv",
        cookies=request.cookies,
        headless=request.headless,
    )
    _clear_creator_data_files()
    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start account monitor crawler")

    job_id = uuid4().hex
    account_monitor_jobs[job_id] = {
        "job_id": job_id,
        "status": "crawling",
        "stage": "账号笔记抓取已启动",
        "report_mode": request.report_mode,
        "creator_count": len(normalized_links),
        "requested_creator_ids": requested_creator_ids,
        "source_started_at": source_started_at,
        "notes_per_creator": notes_per_creator,
        "row_count": 0,
        "report_path": "",
        "pgy_errors": [],
        "pgy_login_required": False,
        "pgy_login_accounts": [],
        "base_token": request.base_token.strip(),
        "table_id": request.table_id.strip(),
        "error": "",
        "started_at": datetime.now().isoformat(),
    }
    account_monitor_jobs[job_id]["task"] = asyncio.create_task(
        _build_account_monitor_report(job_id, request.report_mode, request.base_token.strip(), request.table_id.strip())
    )
    return {
        "status": "ok",
        "message": "账号内容监测任务已启动",
        "job_id": job_id,
        "creator_count": len(normalized_links),
        "notes_per_creator": notes_per_creator,
        "report_mode": request.report_mode,
    }


@router.post("/account-monitor/resume-pgy")
async def resume_account_monitor_pgy(job_id: str):
    """Resume the Pugongying phase from its checkpoint without recrawling Xiaohongshu."""
    job = account_monitor_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到账号内容监测任务，无法从蒲公英断点继续")
    task = job.get("task")
    if task and not task.done():
        raise HTTPException(status_code=400, detail="账号内容监测任务仍在运行")
    if not job.get("pgy_login_required") or not job.get("_pgy_pending_keys"):
        raise HTTPException(status_code=400, detail="当前任务没有待继续的蒲公英登录断点")
    source_path = Path(str(job.get("source_path") or ""))
    if not source_path.is_file():
        raise HTTPException(status_code=410, detail="小红书抓取结果已不存在，请重新执行账号内容监测")

    job.update({
        "status": "enriching",
        "stage": "正在从蒲公英断点继续",
        "pgy_login_required": False,
        "pgy_login_accounts": [],
        "error": "",
    })
    job["task"] = asyncio.create_task(
        _build_account_monitor_report(
            job_id,
            str(job.get("report_mode") or "auto"),
            str(job.get("base_token") or ""),
            str(job.get("table_id") or ""),
            resume_pgy_only=True,
        )
    )
    return {
        "status": "ok",
        "message": "已从蒲公英断点继续，不会重复抓取小红书",
        "job_id": job_id,
        "remaining_creator_count": len(job.get("_pgy_pending_keys") or []),
    }


@router.get("/account-monitor/status")
async def account_monitor_status(job_id: str):
    job = account_monitor_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到账号内容监测任务")
    return {
        key: value
        for key, value in job.items()
        if key not in {
            "task", "report_path", "source_path", "base_token", "table_id",
            "_pgy_summaries", "_pgy_lookups", "_pgy_pending_keys",
        }
    }


def _enrich_sentiment_comment_rows(
    comments: List[Dict[str, Any]],
    notes: List[Dict[str, Any]],
    note_links: str,
) -> List[Dict[str, Any]]:
    note_by_id = {
        str(row.get("note_id") or row.get("id") or "").strip(): row
        for row in notes
        if str(row.get("note_id") or row.get("id") or "").strip()
    }
    normalized_links: Dict[str, str] = {}
    for link in re.split(r"[\n\r,，;；]+", note_links or ""):
        link = link.strip()
        if not link:
            continue
        with contextlib.suppress(ValueError):
            normalized_links[_note_id_from_link(link, resolve_short_link=False)] = link

    enriched: List[Dict[str, Any]] = []
    for comment in comments:
        row = dict(comment)
        note_id = str(row.get("note_id") or "").strip()
        note = note_by_id.get(note_id, {})
        row["笔记链接"] = str(note.get("note_url") or normalized_links.get(note_id) or "")
        row["笔记标题"] = str(note.get("title") or "")
        row["点赞数"] = note.get("liked_count") or note.get("like_count") or ""
        row["评论总数"] = note.get("comment_count") or ""
        row["comment_like_count"] = row.get("like_count") or ""
        enriched.append(row)
    return enriched


async def _build_sentiment_monitor_job(job_id: str, request: NoteSentimentStartRequest, risk_groups: List[Dict[str, Any]]) -> None:
    job = sentiment_monitor_jobs[job_id]
    try:
        job.update({"status": "crawling", "stage": "正在抓取笔记评论", "error": ""})
        if not await _wait_crawler_idle(timeout_sec=1800):
            raise RuntimeError("等待笔记评论抓取完成超时")
        job.update({"status": "configuring", "stage": "正在配置多维表格舆情风险规则"})
        await _ensure_sentiment_monitor_fields(request.base_token, request.table_id, risk_groups)
        crawl_started_at = float(job.get("crawl_started_at") or 0)
        comments_path = _latest_local_file(
            "comments", "detail", modified_after=crawl_started_at, strict_mode=True
        )
        notes_path = _latest_local_file(
            "notes", "detail", modified_after=crawl_started_at, strict_mode=True
        )
        enriched_comments = _enrich_sentiment_comment_rows(
            _read_local_rows(comments_path),
            _read_local_rows(notes_path),
            request.note_links,
        )
        enriched_path = temp_dir() / f"sentiment_comments_{job_id}.jsonl"
        enriched_path.parent.mkdir(parents=True, exist_ok=True)
        enriched_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in enriched_comments),
            encoding="utf-8",
        )
        job.update({"status": "syncing", "stage": "正在同步评论至笔记舆情监控表"})
        try:
            sync_result = await sync_local_to_base(
                LocalToBaseSyncRequest(
                    base_token=request.base_token,
                    table_id=request.table_id,
                    data_type="comments",
                    crawler_type_hint="detail",
                    file_path=str(enriched_path),
                )
            )
        finally:
            enriched_path.unlink(missing_ok=True)
        job.update({
            "status": "completed",
        "stage": "评论已同步，各风险类型由多维表格规则自动标记",
            "sync_result": sync_result,
        })
    except Exception as exc:
        job.update({"status": "error", "stage": "执行失败", "error": str(exc)})


@router.post("/sentiment-monitor/start")
async def start_sentiment_monitor(request: NoteSentimentStartRequest):
    raw_links = [item.strip() for item in re.split(r"[\n\r,，;；]+", request.note_links or "") if item.strip()]
    if not raw_links:
        raise HTTPException(status_code=400, detail="请至少提供 1 个小红书笔记链接")
    try:
        risk_groups = _normalize_sentiment_risk_groups(request.risk_groups, request.risk_keywords)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(status_code=400, detail="当前已有抓取任务在运行，请等待完成后再启动笔记舆情监控")

    note_urls_by_id: Dict[str, str] = {}
    errors: List[str] = []
    for raw_link in raw_links:
        try:
            normalized_url = await asyncio.to_thread(_normalized_note_url_from_link, raw_link)
            note_id = _note_id_from_link(normalized_url, resolve_short_link=False)
            note_urls_by_id.setdefault(note_id, normalized_url)
        except ValueError as exc:
            errors.append(f"{raw_link[:80]}：{exc}")
    if errors:
        raise HTTPException(status_code=400, detail="笔记链接校验失败：" + "；".join(errors[:5]))
    note_urls = list(note_urls_by_id.values())
    if not note_urls:
        raise HTTPException(status_code=400, detail="未识别到有效的小红书笔记链接")

    start_request = CrawlerStartRequest(
        platform="xhs",
        login_type=request.login_type,
        crawler_type="detail",
        specified_ids=",".join(note_urls),
        max_notes_count=len(note_urls),
        max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
        enable_comments=True,
        enable_sub_comments=request.enable_sub_comments,
        enable_media=request.enable_media,
        save_option=request.save_option,
        cookies=request.cookies,
        headless=request.headless,
    )
    crawl_started_at = time.time()
    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="无法启动笔记评论抓取")

    job_id = uuid4().hex
    sentiment_monitor_jobs[job_id] = {
        "job_id": job_id,
        "status": "crawling",
        "stage": "笔记评论抓取已启动",
        "note_count": len(note_urls),
        "risk_groups": risk_groups,
        "sync_result": {},
        "error": "",
        "started_at": datetime.now().isoformat(),
        "crawl_started_at": crawl_started_at,
    }
    sentiment_monitor_jobs[job_id]["task"] = asyncio.create_task(_build_sentiment_monitor_job(job_id, request, risk_groups))
    return {"status": "ok", "message": "笔记舆情监控任务已启动", "job_id": job_id, "note_count": len(note_urls)}


@router.post("/sentiment-monitor/sync-rules")
async def sync_sentiment_monitor_rules(request: SentimentRuleSyncRequest):
    """Apply risk-category changes to Base immediately, without a crawler run."""
    try:
        risk_groups = _normalize_sentiment_risk_groups(request.risk_groups, request.risk_keywords)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _ensure_sentiment_monitor_fields(request.base_token, request.table_id, risk_groups)
    return {
        "status": "ok",
        "message": "风险分组规则已同步至多维表格",
        "risk_groups": [group["name"] for group in risk_groups],
    }


@router.get("/sentiment-monitor/status")
async def sentiment_monitor_status(job_id: str):
    job = sentiment_monitor_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="未找到笔记舆情监控任务")
    return {key: value for key, value in job.items() if key != "task"}


@router.post("/import-sample-accounts")
async def import_sample_accounts(request: SampleAccountImportRequest):
    filename = (request.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件内容不是有效 base64") from exc
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大，请控制在 10MB 以内")

    accounts = _parse_sample_account_file(filename, content)
    if not accounts:
        raise HTTPException(status_code=400, detail="未从文件中识别到小红书主页链接或账号 ID")
    return {
        "status": "ok",
        "filename": filename,
        "count": len(accounts),
        "accounts": accounts,
        "text": "\n".join(accounts),
    }


@router.get("/collaboration-notes-template")
async def collaboration_notes_template():
    output = io.StringIO()
    csv.writer(output).writerow(COLLAB_NOTE_REQUIRED_COLUMNS)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=collaboration_notes_template.csv"},
    )


@router.get("/sentiment-notes-template")
async def sentiment_notes_template():
    output = io.StringIO()
    csv.writer(output).writerow(COLLAB_NOTE_REQUIRED_COLUMNS)
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=sentiment_notes_template.csv"},
    )


@router.post("/import-collaboration-notes")
async def import_collaboration_notes(request: SampleAccountImportRequest):
    filename = (request.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件内容不是有效 base64") from exc
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大，请控制在 10MB 以内")
    rows = _parse_collaboration_note_file(filename, content)
    if not rows:
        raise HTTPException(status_code=400, detail="表单中没有可监测的发布笔记链接")
    links = [row["发布笔记链接"] for row in rows]
    return {
        "status": "ok",
        "filename": filename,
        "count": len(rows),
        "rows": rows,
        "note_links": "\n".join(links),
    }


@router.post("/import-sentiment-notes")
async def import_sentiment_notes(request: SampleAccountImportRequest):
    filename = (request.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="缺少文件名")
    try:
        content = base64.b64decode(request.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件内容不是有效 base64") from exc
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大，请控制在 10MB 以内")
    rows = _parse_collaboration_note_file(filename, content)
    if not rows:
        raise HTTPException(status_code=400, detail="表单中没有可监测的发布笔记链接")
    links = [row["发布笔记链接"] for row in rows]
    return {
        "status": "ok",
        "filename": filename,
        "count": len(rows),
        "rows": rows,
        "note_links": "\n".join(links),
    }


@router.post("/huitun/login")
async def huitun_login(request: HuitunLoginRequest):
    args = ["login-wait", "--login-timeout-ms", str(max(30_000, request.timeout_ms))]
    if request.keep_open:
        args.append("--keep-open")
    result = await _run_huitun_automation(args, timeout_sec=max(60, int(request.timeout_ms / 1000) + 30))
    return result


@router.post("/huitun/screenshot")
async def huitun_screenshot(request: HuitunScreenshotRequest):
    args = ["screenshot", "--url", request.url]
    result = await _run_huitun_automation(args, timeout_sec=90)
    return result


@router.post("/huitun/export-anchor-list")
async def huitun_export_anchor_list(request: HuitunExportAnchorListRequest):
    args = [
        "export-anchor-list",
        "--rank-tab", request.rank_tab.strip() or "涨粉榜",
    ]
    if request.category.strip():
        args.extend(["--category", request.category.strip()])
    if request.screenshot_before_export:
        args.append("--screenshot-before-export")
    if request.keep_open:
        args.append("--keep-open")
    result = await _run_huitun_automation(args, timeout_sec=150)
    return result


@router.post("/pgy/run-kol")
async def pgy_run_kol(request: PgyKolRunRequest):
    if not request.nickname.strip():
        raise HTTPException(status_code=400, detail="请填写达人昵称；小红书号仅用于重名时精确匹配")
    args = ["run-kol", "--api-only"]
    if request.nickname.strip():
        args.extend(["--nickname", request.nickname.strip()])
    if request.red_id.strip():
        args.extend(["--red-id", request.red_id.strip()])
    args.extend(["--similar-detail-limit", str(max(0, min(20, request.similar_detail_limit)))])
    if request.similar_user_ids.strip():
        args.extend(["--similar-user-ids", request.similar_user_ids.strip()])
    if request.keep_open:
        args.append("--keep-open")
    result = await _run_pgy_automation(args, timeout_sec=900)
    if result.get("status") == "login_required":
        raise HTTPException(status_code=401, detail=result.get("error") or "蒲公英需要登录")
    if result.get("status") == "error" or result.get("returncode"):
        raise HTTPException(status_code=400, detail=result.get("error") or result)
    if request.sync_after_run:
        outputs = result.get("outputs") or {}
        sync_result = await _sync_pgy_summary_to_base(
            PgyKolSyncRequest(
                base_token=request.base_token,
                table_id=request.table_id,
                summary_path=outputs.get("summary") or "",
                output_dir=outputs.get("output_dir") or "",
            )
        )
        result["sync"] = sync_result
    return result


@router.post("/pgy/login")
async def pgy_login(request: PgyLoginRequest):
    args = ["login"]
    wait_seconds = max(30, min(1800, int(request.timeout_ms / 1000)))
    args.extend(["--login-wait-seconds", str(wait_seconds)])
    if request.keep_open:
        async with pgy_login_launch_lock:
            project_root = Path(__file__).resolve().parents[2]
            script_path = project_root / "tools" / "pgy_automation.py"
            if _pgy_cdp_available():
                opened_url = _open_url_in_cdp(PGY_CDP_ENDPOINT, PGY_LOGIN_URL)
                browser_focused = _focus_detected_browser()
                return {
                    "status": "login_window_opened",
                    "message": "蒲公英登录窗口已打开",
                    "cdp": PGY_CDP_ENDPOINT,
                    "url": PGY_LOGIN_URL,
                    "opened_url": opened_url,
                    "browser_focused": browser_focused,
                    "wait_seconds": wait_seconds,
                }
            cmd = [
                sys.executable,
                str(script_path),
                *args,
                *_pgy_browser_args(args),
                "--remote-debugging-port",
                str(PGY_CDP_PORT),
                "--detach-hold-open",
            ]
            try:
                pgy_log = project_root / "tmp_logs_pgy_automation.txt"
                pgy_env = {**os.environ, "PYTHONPATH": str(project_root)}
                with contextlib.ExitStack() as stack:
                    stdout_handle = stack.enter_context(open(pgy_log, "w", encoding="utf-8"))
                    stderr_handle = stack.enter_context(open(pgy_log, "a", encoding="utf-8"))
                    proc = subprocess.Popen(
                        cmd,
                        cwd=str(project_root),
                        stdout=stdout_handle,
                        stderr=stderr_handle,
                        stdin=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        env=pgy_env,
                    )
                if not await _wait_for_pgy_cdp(timeout_sec=15.0, process=proc):
                    err = pgy_log.read_text(encoding="utf-8", errors="replace")[:800] if pgy_log.exists() else ""
                    if proc.poll() is not None:
                        raise HTTPException(status_code=500, detail=f"蒲公英登录进程启动后立即退出(code={proc.returncode}): {err}")
                    raise HTTPException(status_code=504, detail=f"蒲公英浏览器启动超时，CDP 端口 {PGY_CDP_PORT} 未就绪: {err}")
                opened_url = _open_url_in_cdp(PGY_CDP_ENDPOINT, PGY_LOGIN_URL)
                browser_focused = _focus_detected_browser()
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=500, detail=f"蒲公英登录窗口启动失败: {exc}") from exc
        return {
            "status": "login_window_opened",
            "message": "蒲公英登录窗口已打开，请在浏览器内完成登录；登录状态每 1 秒检测一次",
            "cdp": PGY_CDP_ENDPOINT,
            "url": PGY_LOGIN_URL,
            "opened_url": opened_url,
            "browser_focused": browser_focused,
            "wait_seconds": wait_seconds,
        }
    result = await _run_pgy_automation(args, timeout_sec=wait_seconds + 45)
    if result.get("status") == "error" or result.get("returncode"):
        raise HTTPException(status_code=400, detail=result.get("error") or result)
    return result


@router.post("/pgy/status")
async def pgy_status():
    result = await _run_pgy_automation(["screenshot"], timeout_sec=90)
    if result.get("status") == "error" or result.get("returncode"):
        raise HTTPException(status_code=400, detail=result.get("error") or result)
    return result


@router.get("/pgy/file")
async def pgy_file(path: str):
    project_root = Path(__file__).resolve().parents[2]
    downloads_root = downloads_dir().resolve()
    file_path = Path(path)
    if not file_path.is_absolute():
        file_path = downloads_root / file_path
    file_path = file_path.resolve()
    try:
        file_path.relative_to(downloads_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="只能预览 downloads 目录下的文件") from exc
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path=str(file_path))


@router.post("/pgy/sync-kol")
async def pgy_sync_kol(request: PgyKolSyncRequest):
    return await _sync_pgy_summary_to_base(request)


@router.post("/setup-scenario-tables")
async def setup_scenario_tables(request: ScenarioTableSetupRequest):
    if not request.base_token:
        raise HTTPException(status_code=400, detail="缺少 base_token")
    account_filter_fields = _account_content_monitor_fields()
    viral_monitor_fields = _viral_monitor_fields()
    note_recreation_fields = _note_recreation_fields()
    comments_fields = _comments_fields()
    collab_comments_fields = _sentiment_monitor_fields()
    collaboration_fields = _note_data_monitor_fields()
    creator_selection_fields = _creator_selection_fields()
    creator_screening_fields = _creator_screening_result_fields()
    existing = await _list_base_tables(request.base_token)
    existing_map = {t["name"]: t["id"] for t in existing}

    async def create_or_reuse(table_name: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        if table_name in existing_map:
            if table_name == request.note_recreation_table_name:
                await _ensure_note_recreation_fields(request.base_token, existing_map[table_name])
            elif table_name == request.account_filter_table_name:
                await _ensure_account_content_monitor_fields(request.base_token, existing_map[table_name])
            elif table_name == request.viral_monitor_table_name:
                await _ensure_viral_monitor_fields(request.base_token, existing_map[table_name])
            elif table_name == request.creator_selection_table_name:
                await _ensure_creator_selection_fields(request.base_token, existing_map[table_name])
            elif table_name == request.creator_screening_table_name:
                await _ensure_fields_and_view_order(request.base_token, existing_map[table_name], fields)
            return {"table_name": table_name, "table_id": existing_map[table_name], "reused": True, "raw": {"table": {"id": existing_map[table_name], "name": table_name}}}
        created = await _create_table_with_fields(request.base_token, table_name, fields)
        if table_name == request.account_filter_table_name:
            await _ensure_account_content_monitor_fields(request.base_token, created["table_id"])
        elif table_name == request.viral_monitor_table_name:
            await _ensure_viral_monitor_fields(request.base_token, created["table_id"])
        elif table_name in {request.creator_selection_table_name, request.creator_screening_table_name}:
            await _set_table_view_field_order(
                request.base_token, created["table_id"], [field["name"] for field in fields],
            )
        created["reused"] = False
        return created

    tables = [
        # Keep fallback creation and the response aligned with the customer's
        # current master Base sheet order. Base copy itself preserves that order.
        await create_or_reuse(request.viral_monitor_table_name, viral_monitor_fields),
        await create_or_reuse(request.comments_table_name, comments_fields),
        await create_or_reuse(request.creator_selection_table_name, creator_selection_fields),
        await create_or_reuse(request.account_filter_table_name, account_filter_fields),
        await create_or_reuse(request.note_recreation_table_name, note_recreation_fields),
        await create_or_reuse(request.collaboration_monitor_table_name, collaboration_fields),
        await create_or_reuse(request.collab_comments_table_name, collab_comments_fields),
        await create_or_reuse(request.creator_screening_table_name, creator_screening_fields),
    ]
    return {"status": "ok", "tables": tables}


@router.get("/base-tables")
async def get_base_tables(base_token: str):
    token = _extract_base_token(base_token)
    if not token:
        raise HTTPException(status_code=400, detail="base_token 不能为空")
    tables = await _list_base_tables(token)
    return {"status": "ok", "base_token": token, "tables": tables}


@router.get("/base-info")
async def get_base_info(base_token: str):
    token = _extract_base_token(base_token)
    if not token:
        raise HTTPException(status_code=400, detail="base_token 不能为空")
    try:
        info = await _get_base_info(token)
    except HTTPException as exc:
        return {"status": "ok", "base_token": token, "name": "", "warning": str(exc.detail)}
    return {"status": "ok", **info}


@router.get("/note-recreation/cases")
async def get_note_recreation_cases(
    base_token: str,
    table_id: str,
    project_name: str = "",
    limit: int = 100,
):
    """Expose project-scoped rewritten note cases for the read-only Web UI."""
    token = _extract_base_token(base_token)
    if not token or not table_id.strip():
        raise HTTPException(status_code=400, detail="缺少笔记二创表的 base_token 或 table_id")
    result = await _read_note_recreation_cases(token, table_id.strip(), project_name, limit)
    return {"status": "ok", **result}


@router.get("/note-recreation/attachment")
async def get_note_recreation_attachment(
    base_token: str,
    table_id: str,
    record_id: str,
    file_token: str,
    filename: str = "",
):
    """Download one Base attachment into a local cache for safe image preview."""
    token = _extract_base_token(base_token)
    if not all((token, table_id.strip(), record_id.strip(), file_token.strip())):
        raise HTTPException(status_code=400, detail="缺少二创封面预览所需的附件参数")
    cache_path, cache_key = _note_recreation_attachment_cache_path(
        token, table_id.strip(), record_id.strip(), file_token.strip(), filename,
    )
    if not cache_path.is_file() or not cache_path.stat().st_size:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        relative_output = f"./.lark_attachment_cache/{cache_key}/{cache_path.name}"
        await _run_lark_cli(
            [
                _find_lark_cli(), "base", "+record-download-attachment", "--as", "user",
                "--base-token", token, "--table-id", table_id.strip(), "--record-id", record_id.strip(),
                "--file-token", file_token.strip(), "--output", relative_output, "--overwrite",
            ],
            timeout_sec=60,
        )
    if not cache_path.is_file() or not cache_path.stat().st_size:
        raise HTTPException(status_code=404, detail="未下载到二创封面附件")
    return FileResponse(path=str(cache_path), filename=cache_path.name)


@router.post("/bootstrap-project")
async def bootstrap_project(request: ScenarioBootstrapRequest):
    if not request.project_name.strip():
        raise HTTPException(status_code=400, detail="项目名不能为空")
    if request.template_base_token.strip():
        base_info = await _copy_base(
            request.template_base_token.strip(),
            request.project_name.strip(),
            request.folder_token.strip(),
            request.time_zone.strip() or "Asia/Shanghai",
        )
        base_token = base_info["base_token"]
        scenario = await setup_scenario_tables(
            ScenarioTableSetupRequest(
                base_token=base_token,
                account_filter_table_name=request.account_filter_table_name,
                viral_monitor_table_name=request.viral_monitor_table_name,
                note_recreation_table_name=request.note_recreation_table_name,
                comments_table_name=request.comments_table_name,
                collaboration_monitor_table_name=request.collaboration_monitor_table_name,
                collab_comments_table_name=request.collab_comments_table_name,
                creator_selection_table_name=request.creator_selection_table_name,
                creator_screening_table_name=request.creator_screening_table_name,
            )
        )
        return {
            "status": "ok",
            "project_name": request.project_name.strip(),
            "base_token": base_token,
            "template_base_token": base_info.get("template_base_token", ""),
            "root_table": None,
            "tables": scenario.get("tables", []),
            "base_raw": base_info.get("raw", {}),
        }
    base_info = await _create_base(request.project_name.strip(), request.folder_token.strip(), request.time_zone.strip() or "Asia/Shanghai")
    base_token = base_info["base_token"]
    root_fields = [{"name": "项目名", "type": "text"}, {"name": "项目状态", "type": "text"}, {"name": "负责人", "type": "text"}, {"name": "监控关键词", "type": "text"}]
    root_table = await _create_table_with_fields(base_token, request.root_table_name, root_fields)
    scenario = await setup_scenario_tables(
        ScenarioTableSetupRequest(
            base_token=base_token,
            account_filter_table_name=request.account_filter_table_name,
            viral_monitor_table_name=request.viral_monitor_table_name,
            note_recreation_table_name=request.note_recreation_table_name,
            comments_table_name=request.comments_table_name,
            collaboration_monitor_table_name=request.collaboration_monitor_table_name,
            collab_comments_table_name=request.collab_comments_table_name,
            creator_selection_table_name=request.creator_selection_table_name,
            creator_screening_table_name=request.creator_screening_table_name,
        )
    )
    return {"status": "ok", "project_name": request.project_name.strip(), "base_token": base_token, "root_table": root_table, "tables": scenario.get("tables", []), "base_raw": base_info.get("raw", {})}


@router.post("/sync-local-to-base")
async def sync_local_to_base(request: LocalToBaseSyncRequest):
    field_defs = await _read_table_field_defs(request.base_token, request.table_id)
    field_names = {str(field.get("name")) for field in field_defs if field.get("name")}
    if request.data_type == "notes" and not field_names.intersection(_base_dedupe_field_candidates("notes")):
        await _create_base_field(request.base_token, request.table_id, _text_field("笔记ID"))
        await _create_base_field(request.base_token, request.table_id, _text_field("笔记链接"))
        field_defs = await _read_table_field_defs(request.base_token, request.table_id)
    field_defs_by_name = {
        str(field.get("name")): field
        for field in field_defs
        if field.get("name")
    }
    table_fields = [
        field.get("name")
        for field in field_defs
        if field.get("name") and field.get("type") not in {
            "not_support", "attachment", "formula", "lookup", "auto_number",
            "created_at", "updated_at", "created_by", "updated_by",
            "created_time", "modified_time", "created_user", "modified_user",
        }
    ]
    attachment_field_ids = {
        str(field.get("name")): str(field.get("id"))
        for field in field_defs
        if field.get("type") == "attachment" and field.get("name") and field.get("id")
    }
    if not table_fields:
        raise HTTPException(status_code=400, detail="目标数据表没有可用字段")
    file_path = Path(request.file_path) if request.file_path else _latest_local_file(request.data_type, request.crawler_type_hint)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"本地文件不存在: {file_path}")
    row_items: List[Dict[str, Any]] = []
    local_rows = _read_local_rows(file_path)
    requested_keywords = {
        keyword.strip()
        for keyword in re.split(r"[,，\r\n]+", request.source_keyword)
        if keyword.strip()
    }
    scoped_note_ids: set[str] | None = None
    if requested_keywords and request.data_type == "comments" and request.crawler_type_hint == "search":
        notes_file = _related_notes_file(file_path, request.crawler_type_hint)
        scoped_note_ids = {
            str(note.get("note_id") or note.get("笔记ID") or "").strip()
            for note in _read_local_rows(notes_file)
            if str(note.get("source_keyword") or note.get("关键词") or "").strip() in requested_keywords
        }
        scoped_note_ids.discard("")
    for obj in local_rows:
        # Creator-mode rows usually do not contain source_keyword; do not over-filter them.
        if requested_keywords:
            row_keyword = str(obj.get("source_keyword", "")).strip()
            if request.crawler_type_hint == "search":
                if row_keyword:
                    if row_keyword not in requested_keywords:
                        continue
                elif request.data_type == "comments":
                    row_note_id = str(obj.get("note_id") or obj.get("笔记ID") or "").strip()
                    if scoped_note_ids is None or row_note_id not in scoped_note_ids:
                        continue
                else:
                    continue
            if request.crawler_type_hint != "search" and row_keyword and row_keyword not in requested_keywords:
                continue
        if request.project_name:
            obj["项目名"] = request.project_name
            obj["所属项目"] = request.project_name
        if request.source_keyword and not (obj.get("source_keyword") or obj.get("关键词")):
            obj["source_keyword"] = request.source_keyword
            obj["关键词"] = request.source_keyword
        raw_values = _row_to_table_values(obj, table_fields, request.data_type)
        row_items.append({
            "source": obj,
            "values": _normalize_base_row_values(table_fields, raw_values, field_defs_by_name),
        })
    if request.data_type == "notes":
        liked_idx = table_fields.index("liked_count") if "liked_count" in table_fields else -1
        if liked_idx >= 0:
            row_items.sort(key=lambda x: int(x["values"][liked_idx]) if isinstance(x["values"][liked_idx], int) else 0, reverse=True)
    if request.limit and request.limit > 0:
        row_items = row_items[:request.limit]
    if not row_items:
        raise HTTPException(status_code=400, detail="未找到可同步的数据（请检查关键词/文件类型）")
    lark_cli_bin = _find_lark_cli()
    dedupe_fields = _base_dedupe_field_candidates(request.data_type)
    dedupe_field = next((field_name for field_name in dedupe_fields if field_name in table_fields), dedupe_fields[0])
    existing = await _read_existing_base_records(request.base_token, request.table_id, dedupe_fields)
    created = 0
    updated = 0
    attachment_uploads = 0
    attachment_errors: List[str] = []
    dedupe_idx = table_fields.index(dedupe_field) if dedupe_field in table_fields else -1
    rows_to_create: List[Dict[str, Any]] = []
    for item in row_items:
        row_values = item["values"]
        dedupe_key = str(row_values[dedupe_idx]).strip() if dedupe_idx >= 0 and row_values[dedupe_idx] else ""
        record_id = existing.get(dedupe_key) if dedupe_key else None
        if record_id:
            payload = _base_record_field_map(table_fields, row_values)
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [lark_cli_bin, "base", "+record-upsert", "--as", "user", "--base-token", request.base_token, "--table-id", request.table_id, "--record-id", record_id, "--json", json_arg],
                    timeout_sec=60,
                )
            updated += 1
            uploaded, error = await _upload_cover_file_if_available(request.base_token, request.table_id, record_id, item["source"], attachment_field_ids)
            if uploaded:
                attachment_uploads += 1
            elif error:
                attachment_errors.append(error)
        else:
            rows_to_create.append(item)
    for batch in _chunk_table_rows([item["values"] for item in rows_to_create]):
        batch_start = created
        batch_items = rows_to_create[batch_start:batch_start + len(batch)]
        payload = {"fields": table_fields, "rows": batch}
        with _lark_json_arg(payload) as json_arg:
            created_payload = await _run_lark_cli(
                [lark_cli_bin, "base", "+record-batch-create", "--as", "user", "--base-token", request.base_token, "--table-id", request.table_id, "--json", json_arg],
                timeout_sec=60,
            )
        record_ids = [str(record_id) for record_id in ((created_payload.get("data") or {}).get("record_id_list") or []) if record_id]
        for record_id, item in zip(record_ids, batch_items):
            uploaded, error = await _upload_cover_file_if_available(request.base_token, request.table_id, record_id, item["source"], attachment_field_ids)
            if uploaded:
                attachment_uploads += 1
            elif error:
                attachment_errors.append(error)
        created += len(batch)
    target_url = f"https://my.feishu.cn/base/{request.base_token}?table={request.table_id}"
    return {
        "status": "ok",
        "message": "Local data synced to Base",
        "base_token": request.base_token,
        "table_id": request.table_id,
        "target_url": target_url,
        "file": str(file_path),
        "fields": table_fields,
        "created": created,
        "updated": updated,
        "attachment_uploads": attachment_uploads,
        "attachment_errors": attachment_errors[:10],
    }


@router.post("/collaboration-monitor/start")
async def start_collaboration_monitor(request: CollaborationMonitorStartRequest):
    if not request.note_links.strip() and not _split_creator_inputs(request.creator_ids):
        raise HTTPException(status_code=400, detail="请先导入合作笔记表单，至少提供 1 条发布笔记链接")
    await _refresh_collab_creator_notes(request)
    notes_result = await _sync_collaboration_snapshot(request, f"{request.interval_hours}h")
    comments_result = await _sync_collaboration_comments(request, f"{request.interval_hours}h")
    job_id = f"collab-{uuid4().hex[:8]}"
    task = asyncio.create_task(_collaboration_job_loop(job_id, request))
    collaboration_monitor_jobs[job_id] = {"job_id": job_id, "interval_hours": request.interval_hours, "started_at": datetime.now().isoformat(), "last_run_at": "", "last_result": {"notes": notes_result, "comments": comments_result}, "last_error": "", "project_name": request.project_name, "source_keyword": request.source_keyword, "table_id": request.table_id, "task": task}
    return {"status": "ok", "message": "合作笔记监控已启动", "job_id": job_id, "notes": notes_result, "comments": comments_result}


@router.post("/collaboration-monitor/crawl-once")
async def collaboration_monitor_crawl_once(request: CollaborationMonitorStartRequest):
    if not request.note_links.strip() and not _split_creator_inputs(request.creator_ids):
        raise HTTPException(status_code=400, detail="请先导入合作笔记表单，至少提供 1 条发布笔记链接")
    await _refresh_collab_creator_notes(request)
    notes_result = await _sync_collaboration_snapshot(request, "manual")
    comments_result = await _sync_collaboration_comments(request, "manual")
    return {"status": "ok", "message": "合作笔记抓取同步完成", "notes": notes_result, "comments": comments_result}


@router.post("/collaboration-monitor/stop")
async def stop_collaboration_monitor(request: CollaborationMonitorStopRequest):
    job = collaboration_monitor_jobs.get(request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"监控任务不存在: {request.job_id}")
    task = job.get("task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    collaboration_monitor_jobs.pop(request.job_id, None)
    return {"status": "ok", "message": "合作笔记监控已停止", "job_id": request.job_id}


@router.get("/collaboration-monitor/status")
async def collaboration_monitor_status():
    jobs = []
    for job_id, data in collaboration_monitor_jobs.items():
        jobs.append({"job_id": job_id, "interval_hours": data.get("interval_hours"), "started_at": data.get("started_at"), "last_run_at": data.get("last_run_at"), "last_result": data.get("last_result"), "last_error": data.get("last_error"), "project_name": data.get("project_name"), "source_keyword": data.get("source_keyword"), "table_id": data.get("table_id"), "running": bool(data.get("task") and not data["task"].done())})
    return {"jobs": jobs}


@router.post("/start-from-rule")
async def start_crawler_from_rule(request: RuleTableStartRequest):
    rules = await _read_rule_table(request.base_token, request.table_id)
    if not rules:
        raise HTTPException(status_code=400, detail="规则表为空")
    selected = None
    if request.rule_name:
        for r in rules:
            if str(r.get("规则名称", "")).strip() == request.rule_name.strip():
                selected = r
                break
        if not selected:
            raise HTTPException(status_code=400, detail=f"未找到规则名称: {request.rule_name}")
    else:
        for r in rules:
            if _rule_is_enabled(r.get("启用")):
                selected = r
                break
        if not selected:
            raise HTTPException(status_code=400, detail="未找到启用状态为“是/true/1”的规则")
    start_request = CrawlerStartRequest(
        platform=request.platform, login_type=request.login_type, crawler_type=request.crawler_type, keywords=str(selected.get("关键词", "") or ""),
        start_page=request.start_page, max_notes_count=request.max_notes_count, enable_comments=request.enable_comments, enable_sub_comments=request.enable_sub_comments,
        save_option=request.save_option, cookies=request.cookies, headless=request.headless,
        xhs_sort_by=str(selected.get("排序", "综合") or "综合"), xhs_note_type=str(selected.get("笔记类型", "不限") or "不限"),
        xhs_publish_time=str(selected.get("发布时间", "不限") or "不限"), xhs_search_scope=str(selected.get("搜索范围", "不限") or "不限"), xhs_location=str(selected.get("位置距离", "不限") or "不限"),
    )
    if not start_request.keywords:
        raise HTTPException(status_code=400, detail="规则缺少关键词，无法启动")
    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")
    return {"status": "ok", "message": "Crawler started from rule successfully", "rule_used": {"规则名称": selected.get("规则名称"), "关键词": selected.get("关键词"), "笔记类型": selected.get("笔记类型"), "发布时间": selected.get("发布时间"), "排序": selected.get("排序")}}


@router.post("/stop")
async def stop_crawler():
    success = await crawler_manager.stop()
    if not success:
        if not crawler_manager.process or crawler_manager.process.poll() is not None:
            raise HTTPException(status_code=400, detail="No crawler is running")
        raise HTTPException(status_code=500, detail="Failed to stop crawler")
    return {"status": "ok", "message": "Crawler stopped successfully"}


@router.get("/status", response_model=CrawlerStatusResponse)
async def get_crawler_status():
    return crawler_manager.get_status()


@router.get("/health")
async def health_check():
    checks = []
    checks.append({"name": "API Server", "ok": True, "message": "Running"})
    cdp_ok = False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 9222))
        s.close()
        cdp_ok = True
    except Exception:
        pass
    checks.append({"name": "Chrome CDP", "ok": cdp_ok, "message": "Accessible on port 9222" if cdp_ok else "Not accessible"})
    lark_ok = shutil.which("lark-cli") is not None
    checks.append({"name": "lark-cli", "ok": lark_ok, "message": "Available" if lark_ok else "Not found"})
    return {"status": "ok", "checks": checks}


@router.post("/restart")
async def restart_crawler():
    await crawler_manager.stop()
    return {"status": "ok", "message": "Crawler process restarted"}


@router.get("/logs")
async def get_logs(limit: int = 100):
    logs = crawler_manager.logs[-limit:] if limit > 0 else crawler_manager.logs
    return {"logs": [log.model_dump() for log in logs]}
