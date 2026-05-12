# -*- coding: utf-8 -*-
import contextlib
import json
import asyncio
import shutil
import subprocess
import tempfile
import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from ..schemas import (
    CrawlerStartRequest,
    CrawlerStatusResponse,
    RuleTableStartRequest,
    LocalToBaseSyncRequest,
    SampleCreatorStartRequest,
    ScenarioTableSetupRequest,
    ScenarioBootstrapRequest,
    CollaborationMonitorStartRequest,
    CollaborationMonitorStopRequest,
)
from ..services import crawler_manager
import config

router = APIRouter(prefix="/crawler", tags=["crawler"])
collaboration_monitor_jobs: Dict[str, Dict[str, Any]] = {}


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


async def _run_lark_cli(cmd: List[str], timeout_sec: int = 30) -> Dict[str, Any]:
    project_root = Path(__file__).resolve().parents[2]
    def _is_rate_limited(text: str) -> bool:
        t = (text or "").lower()
        return "800004135" in t or " limited" in t or "rate limit" in t

    max_retries = 3
    wait_seconds = 2
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout_sec,
                check=False,
                cwd=str(project_root),
            )
        except FileNotFoundError:
            raise HTTPException(status_code=400, detail="未找到 lark-cli，请先安装并完成授权")
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail=f"lark-cli 调用超时（{timeout_sec}s）")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"lark-cli 调用异常: {e}")

        if result.returncode != 0:
            err_msg = (result.stderr or result.stdout or "")[:1200]
            last_err = err_msg
            if _is_rate_limited(err_msg) and attempt < max_retries:
                await asyncio.sleep(wait_seconds * (attempt + 1))
                continue
            if _is_rate_limited(err_msg):
                raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {err_msg[:400]}")
            raise HTTPException(status_code=400, detail=f"lark-cli 调用失败: {err_msg[:400]}")

        try:
            payload = json.loads(result.stdout)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"lark-cli 返回解析失败: {e}")

        if payload.get("ok"):
            return payload
        payload_text = json.dumps(payload, ensure_ascii=False)
        last_err = payload_text
        if _is_rate_limited(payload_text) and attempt < max_retries:
            await asyncio.sleep(wait_seconds * (attempt + 1))
            continue
        if _is_rate_limited(payload_text):
            raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {payload_text[:400]}")
        raise HTTPException(status_code=400, detail=f"lark-cli 返回失败: {payload}")
    raise HTTPException(status_code=400, detail=f"lark-cli 调用失败: {last_err[:400]}")


async def _create_table_with_fields(base_token: str, table_name: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = await _run_lark_cli(
        [
            shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli",
            "base", "+table-create",
            "--base-token", base_token,
            "--name", table_name,
            "--fields", json.dumps(fields, ensure_ascii=False),
        ],
        timeout_sec=30,
    )
    return {"table_name": table_name, "table_id": _extract_table_id(payload), "raw": payload.get("data", {})}


async def _create_base(project_name: str, folder_token: str = "", time_zone: str = "Asia/Shanghai") -> Dict[str, Any]:
    cmd = [
        shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli",
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


async def _list_base_tables(base_token: str) -> List[Dict[str, str]]:
    payload = await _run_lark_cli(
        [
            shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli",
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


async def _read_table_fields(base_token: str, table_id: str) -> List[str]:
    payload = await _run_lark_cli(
        [
            shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli",
            "base", "+field-list",
            "--base-token", base_token,
            "--table-id", table_id,
        ],
        timeout_sec=30,
    )
    fields = payload.get("data", {}).get("fields", [])
    return [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]


def _latest_local_file(data_type: str, crawler_type_hint: str = "") -> Path:
    project_root = Path(__file__).resolve().parents[2]
    suffix = "contents" if data_type == "notes" else "comments"
    data_root = project_root / "data" / "xhs"
    mode = (crawler_type_hint or "").strip()
    patterns: List[tuple[str, str]] = []
    if mode:
        patterns.extend([
            ("jsonl", f"{mode}_{suffix}_*.jsonl"),
            ("csv", f"{mode}_{suffix}_*.csv"),
            ("json", f"{mode}_{suffix}_*.json"),
            ("excel", f"{mode}_{suffix}_*.xlsx"),
            ("excel", f"{mode}_{suffix}_*.xls"),
        ])
    patterns.extend([
        # Fallback: support all crawler modes (search/creator/detail/...).
        ("jsonl", f"*_{suffix}_*.jsonl"),
        ("csv", f"*_{suffix}_*.csv"),
        ("json", f"*_{suffix}_*.json"),
        ("excel", f"*_{suffix}_*.xlsx"),
        ("excel", f"*_{suffix}_*.xls"),
    ])

    candidates: List[Path] = []
    for folder, pattern in patterns:
        dir_path = data_root / folder
        if not dir_path.exists():
            continue
        candidates.extend(dir_path.glob(pattern))
    candidates = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"未找到本地 {data_type} 数据文件（jsonl/csv/json/xlsx/xls）")
    return candidates[0]


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


def _row_to_table_values(row: Dict[str, Any], table_fields: List[str], data_type: str) -> List[Any]:
    alias_map = {
        "标题": ["title", "笔记标题"],
        "笔记标题": ["title", "标题"],
        "内容": ["desc", "content", "note_content"],
        "博主名": ["author_nickname", "nickname"],
        "账号": ["author_nickname", "nickname", "博主名"],
        "账号ID": ["author_user_id", "user_id", "author_id"],
        "账号主页": ["author_homepage_url", "author_profile_url", "博主主页"],
        "博主主页": ["author_homepage_url", "author_profile_url"],
        "笔记链接": ["note_url"],
        "笔记ID": ["note_id", "id"],
        "关键词": ["source_keyword", "搜索关键词"],
        "搜索关键词": ["source_keyword"],
        "笔记类型": ["note_type", "type"],
        "封面图": ["image_list", "cover", "cover_url"],
        "话题标签": ["tag_list", "topics"],
        "点赞量": ["liked_count", "like_count", "点赞数"],
        "点赞数": ["liked_count", "like_count", "点赞量"],
        "收藏量": ["collected_count", "收藏数"],
        "收藏数": ["collected_count", "收藏量"],
        "评论量": ["comment_count", "评论数"],
        "评论数": ["comment_count", "评论量"],
        "分享量": ["share_count", "分享数"],
        "分享数": ["share_count", "分享量"],
        "博主粉丝数": ["author_fans", "author_fans_count", "fans_count"],
        "首发时间": ["time", "create_time", "发布时间"],
        "采集时间": ["last_update_time", "crawl_time", "抓取时间"],
        "author_nickname": ["nickname"], "author_user_id": ["user_id"], "comment_user_id": ["user_id"], "comment_user_nickname": ["nickname"],
    }
    numeric_fields = {
        "liked_count", "collected_count", "comment_count", "share_count", "like_count", "create_time",
        "点赞量", "收藏量", "评论量", "分享量",
        "点赞数", "收藏数", "评论数", "分享数",
        "博主粉丝数",
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
        if field_name in {"author_homepage_url", "账号主页", "博主主页"} and value == "":
            author_id = row.get("author_user_id") or row.get("user_id") or row.get("账号ID")
            value = f"https://www.xiaohongshu.com/user/profile/{author_id}" if author_id else ""
        if field_name in numeric_fields:
            try:
                value = int(str(value))
            except Exception:
                value = 0
        values.append(value)
    return values


async def _read_rule_table(base_token: str, table_id: str) -> List[Dict]:
    if not base_token or not table_id:
        raise HTTPException(status_code=400, detail="缺少 base_token 或 table_id")
    lark_cli_bin = shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"
    cmd = [lark_cli_bin, "base", "+record-list", "--base-token", base_token, "--table-id", table_id, "--limit", "200"]
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
    creator_id_list = _split_creator_inputs(request.creator_ids)
    if not creator_id_list:
        return
    if crawler_manager.process and crawler_manager.process.poll() is None:
        raise HTTPException(status_code=400, detail="当前有任务正在运行，无法启动合作监控抓取")
    start_request = CrawlerStartRequest(
        platform="xhs", login_type=request.login_type, crawler_type="creator",
        creator_ids=",".join(creator_id_list), max_notes_count=max(1, request.notes_per_creator),
        max_comments_count_singlenotes=max(1, request.max_comments_count_singlenotes),
        enable_comments=request.enable_comments, enable_sub_comments=request.enable_sub_comments,
        save_option=request.save_option, cookies=request.cookies, headless=request.headless,
    )
    success = await crawler_manager.start(start_request)
    if not success:
        raise HTTPException(status_code=500, detail="合作监控抓取任务启动失败")
    if not await _wait_crawler_idle(timeout_sec=1800):
        raise HTTPException(status_code=504, detail="合作监控抓取超时（30分钟）")


async def _sync_collaboration_snapshot(request: CollaborationMonitorStartRequest, monitor_tag: str) -> Dict[str, Any]:
    table_fields = await _read_table_fields(request.base_token, request.table_id)
    if not table_fields:
        raise HTTPException(status_code=400, detail="合作监控表没有可用字段")
    file_path = Path(request.file_path) if request.file_path else _latest_local_file("notes", "creator")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"本地文件不存在: {file_path}")
    all_rows = _read_jsonl_rows(file_path)
    creator_filters = set()
    for cid in _split_creator_inputs(request.creator_ids):
        if "/user/profile/" in cid:
            creator_filters.add(cid.split("/user/profile/")[-1].split("?")[0].strip())
        else:
            creator_filters.add(cid.strip())
    rows: List[List[Any]] = []
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
        row["项目名"] = request.project_name
        row["所属项目"] = request.project_name
        row["监控周期"] = monitor_tag
        rows.append(_row_to_table_values(row, table_fields, "notes"))
    if request.sync_limit and request.sync_limit > 0:
        rows = rows[:request.sync_limit]
    if not rows:
        raise HTTPException(status_code=400, detail="合作监控未命中可同步数据")
    lark_cli_bin = shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"
    created = 0
    for i in range(0, len(rows), 200):
        payload = {"fields": table_fields, "rows": rows[i:i + 200]}
        await _run_lark_cli(
            [lark_cli_bin, "base", "+record-batch-create", "--base-token", request.base_token, "--table-id", request.table_id, "--json", json.dumps(payload, ensure_ascii=False)],
            timeout_sec=60,
        )
        created += len(payload["rows"])
    return {"created": created, "file": str(file_path)}


async def _collaboration_job_loop(job_id: str, request: CollaborationMonitorStartRequest) -> None:
    interval_seconds = request.interval_hours * 3600
    monitor_tag = f"{request.interval_hours}h"
    while True:
        job = collaboration_monitor_jobs.get(job_id)
        if not job:
            return
        job["last_run_at"] = datetime.now().isoformat()
        try:
            await _refresh_collab_creator_notes(request)
            job["last_result"] = await _sync_collaboration_snapshot(request, monitor_tag)
            job["last_error"] = ""
        except Exception as e:
            job["last_error"] = str(e)
        await asyncio.sleep(interval_seconds)


@router.get("/preflight")
async def preflight_check(keyword: str = "测试"):
    """
    Pre-crawl login state preflight check.
    Grabs cookies from CDP browser via DevTools Protocol, runs pong + keyword dry-run.
    Returns pass/fail so the frontend can block task start if login is stale.
    """
    import httpx as _httpx
    from tools import utils as t_utils

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
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
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
        save_option=request.save_option, cookies=request.cookies, headless=request.headless,
    )
    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start sample creator crawler")
    return {"status": "ok", "message": "样本账号抓取任务已启动", "creator_count": len(creator_id_list), "notes_per_creator": start_request.max_notes_count}


@router.post("/setup-scenario-tables")
async def setup_scenario_tables(request: ScenarioTableSetupRequest):
    if not request.base_token:
        raise HTTPException(status_code=400, detail="缺少 base_token")
    account_filter_fields = [{"name": "项目名", "type": "text"}, {"name": "搜索关键词", "type": "text"}, {"name": "账号", "type": "text"}, {"name": "账号ID", "type": "text"}, {"name": "账号主页", "type": "url"}, {"name": "笔记标题", "type": "text"}, {"name": "笔记链接", "type": "url"}, {"name": "点赞量", "type": "number"}, {"name": "评论量", "type": "number"}, {"name": "收藏量", "type": "number"}, {"name": "语义标签", "type": "text"}, {"name": "推荐理由", "type": "text"}]
    # Align with 179b-like structure for viral monitoring, while keeping fields writable by sync flow.
    viral_monitor_fields = [
        {"name": "项目名", "type": "text"},
        {"name": "关键词", "type": "text"},
        {"name": "标题", "type": "text"},
        {"name": "内容", "type": "text"},
        {"name": "笔记链接", "type": "url"},
        {"name": "笔记ID", "type": "text"},
        {"name": "博主名", "type": "text"},
        {"name": "博主主页", "type": "url"},
        {"name": "博主粉丝数", "type": "number"},
        {"name": "笔记类型", "type": "text"},
        {"name": "封面图", "type": "text"},
        {"name": "话题标签", "type": "text"},
        {"name": "点赞数", "type": "number"},
        {"name": "评论数", "type": "number"},
        {"name": "收藏数", "type": "number"},
        {"name": "分享数", "type": "number"},
        {"name": "首发时间", "type": "datetime"},
        {"name": "采集时间", "type": "datetime"},
        {"name": "当日使用标记", "type": "text"},
        {"name": "已使用账号记录", "type": "text"},
    ]
    collaboration_fields = [{"name": "项目名", "type": "text"}, {"name": "监控周期", "type": "text"}, {"name": "搜索关键词", "type": "text"}, {"name": "笔记标题", "type": "text"}, {"name": "笔记链接", "type": "url"}, {"name": "博主名", "type": "text"}, {"name": "点赞量", "type": "number"}, {"name": "评论量", "type": "number"}, {"name": "收藏量", "type": "number"}, {"name": "分享量", "type": "number"}, {"name": "抓取时间", "type": "datetime"}]
    existing = await _list_base_tables(request.base_token)
    existing_map = {t["name"]: t["id"] for t in existing}

    async def create_or_reuse(table_name: str, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
        if table_name in existing_map:
            return {"table_name": table_name, "table_id": existing_map[table_name], "reused": True, "raw": {"table": {"id": existing_map[table_name], "name": table_name}}}
        created = await _create_table_with_fields(request.base_token, table_name, fields)
        created["reused"] = False
        return created

    tables = [
        await create_or_reuse(request.account_filter_table_name, account_filter_fields),
        await create_or_reuse(request.viral_monitor_table_name, viral_monitor_fields),
        await create_or_reuse(request.collaboration_monitor_table_name, collaboration_fields),
    ]
    return {"status": "ok", "tables": tables}


@router.get("/base-tables")
async def get_base_tables(base_token: str):
    token = (base_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="base_token 不能为空")
    tables = await _list_base_tables(token)
    return {"status": "ok", "base_token": token, "tables": tables}


@router.post("/bootstrap-project")
async def bootstrap_project(request: ScenarioBootstrapRequest):
    if not request.project_name.strip():
        raise HTTPException(status_code=400, detail="项目名不能为空")
    base_info = await _create_base(request.project_name.strip(), request.folder_token.strip(), request.time_zone.strip() or "Asia/Shanghai")
    base_token = base_info["base_token"]
    root_fields = [{"name": "项目名", "type": "text"}, {"name": "项目状态", "type": "text"}, {"name": "负责人", "type": "text"}, {"name": "监控关键词", "type": "text"}]
    root_table = await _create_table_with_fields(base_token, request.root_table_name, root_fields)
    scenario = await setup_scenario_tables(
        ScenarioTableSetupRequest(
            base_token=base_token,
            account_filter_table_name=request.account_filter_table_name,
            viral_monitor_table_name=request.viral_monitor_table_name,
            collaboration_monitor_table_name=request.collaboration_monitor_table_name,
        )
    )
    return {"status": "ok", "project_name": request.project_name.strip(), "base_token": base_token, "root_table": root_table, "tables": scenario.get("tables", []), "base_raw": base_info.get("raw", {})}


@router.post("/sync-local-to-base")
async def sync_local_to_base(request: LocalToBaseSyncRequest):
    table_fields = await _read_table_fields(request.base_token, request.table_id)
    if not table_fields:
        raise HTTPException(status_code=400, detail="目标数据表没有可用字段")
    file_path = Path(request.file_path) if request.file_path else _latest_local_file(request.data_type, request.crawler_type_hint)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"本地文件不存在: {file_path}")
    rows: List[List[Any]] = []
    local_rows = _read_local_rows(file_path)
    for obj in local_rows:
        # Creator-mode rows usually do not contain source_keyword; do not over-filter them.
        if request.source_keyword:
            row_keyword = str(obj.get("source_keyword", "")).strip()
            if row_keyword and row_keyword != request.source_keyword:
                continue
        if request.project_name:
            obj["项目名"] = request.project_name
            obj["所属项目"] = request.project_name
        rows.append(_row_to_table_values(obj, table_fields, request.data_type))
    if request.data_type == "notes":
        liked_idx = table_fields.index("liked_count") if "liked_count" in table_fields else -1
        if liked_idx >= 0:
            rows.sort(key=lambda x: int(x[liked_idx]) if isinstance(x[liked_idx], int) else 0, reverse=True)
    if request.limit and request.limit > 0:
        rows = rows[:request.limit]
    if not rows:
        raise HTTPException(status_code=400, detail="未找到可同步的数据（请检查关键词/文件类型）")
    lark_cli_bin = shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"
    created = 0
    for i in range(0, len(rows), 200):
        payload = {"fields": table_fields, "rows": rows[i:i + 200]}
        tmp_json_path = ""
        project_root = Path(__file__).resolve().parents[2]
        try:
            # lark-cli requires --json @file to be a relative path under current working dir.
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                suffix=".json",
                delete=False,
                dir=str(project_root),
                prefix=".lark_payload_",
            ) as tmp_file:
                json.dump(payload, tmp_file, ensure_ascii=False)
                tmp_json_path = tmp_file.name
            tmp_json_rel = f".\\{Path(tmp_json_path).name}"
            await _run_lark_cli(
                [
                    lark_cli_bin,
                    "base",
                    "+record-batch-create",
                    "--base-token",
                    request.base_token,
                    "--table-id",
                    request.table_id,
                    "--json",
                    f"@{tmp_json_rel}",
                ],
                timeout_sec=60,
            )
        finally:
            if tmp_json_path:
                with contextlib.suppress(Exception):
                    Path(tmp_json_path).unlink(missing_ok=True)
        created += len(payload["rows"])
    return {"status": "ok", "message": "Local data synced to Base", "file": str(file_path), "fields": table_fields, "created": created}


@router.post("/collaboration-monitor/start")
async def start_collaboration_monitor(request: CollaborationMonitorStartRequest):
    await _refresh_collab_creator_notes(request)
    await _sync_collaboration_snapshot(request, f"{request.interval_hours}h")
    job_id = f"collab-{uuid4().hex[:8]}"
    task = asyncio.create_task(_collaboration_job_loop(job_id, request))
    collaboration_monitor_jobs[job_id] = {"job_id": job_id, "interval_hours": request.interval_hours, "started_at": datetime.now().isoformat(), "last_run_at": "", "last_result": {"created": 0}, "last_error": "", "project_name": request.project_name, "source_keyword": request.source_keyword, "table_id": request.table_id, "task": task}
    return {"status": "ok", "message": "合作笔记监控已启动", "job_id": job_id}


@router.post("/collaboration-monitor/stop")
async def stop_collaboration_monitor(request: CollaborationMonitorStopRequest):
    job = collaboration_monitor_jobs.get(request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"监控任务不存在: {request.job_id}")
    task = job.get("task")
    if task:
        task.cancel()
        with contextlib.suppress(Exception):
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


@router.get("/logs")
async def get_logs(limit: int = 100):
    logs = crawler_manager.logs[-limit:] if limit > 0 else crawler_manager.logs
    return {"logs": [log.model_dump() for log in logs]}
