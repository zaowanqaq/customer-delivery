# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/api/routers/crawler.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import json
import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from ..schemas import CrawlerStartRequest, CrawlerStatusResponse, RuleTableStartRequest, LocalToBaseSyncRequest
from ..services import crawler_manager

router = APIRouter(prefix="/crawler", tags=["crawler"])


def _rule_is_enabled(value) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"是", "true", "1", "yes", "y", "enabled", "on"}


async def _run_lark_cli(cmd: List[str], timeout_sec: int = 30) -> Dict[str, Any]:
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
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="未找到 lark-cli，请先安装并完成授权")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"lark-cli 调用超时（{timeout_sec}s）")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"lark-cli 调用异常: {e}")

    if result.returncode != 0:
        err_msg = result.stderr or result.stdout
        raise HTTPException(status_code=400, detail=f"lark-cli 调用失败: {err_msg[:400]}")

    try:
        payload = json.loads(result.stdout)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"lark-cli 返回解析失败: {e}")
    if not payload.get("ok"):
        raise HTTPException(status_code=400, detail=f"lark-cli 返回失败: {payload}")
    return payload


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


def _latest_local_file(data_type: str) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    suffix = "contents" if data_type == "notes" else "comments"
    jsonl_dir = project_root / "data" / "xhs" / "jsonl"
    candidates = sorted(jsonl_dir.glob(f"search_{suffix}_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"未找到本地 {data_type} 数据文件（jsonl）")
    return candidates[0]


def _row_to_table_values(row: Dict[str, Any], table_fields: List[str], data_type: str) -> List[Any]:
    alias_map = {
        "标题": ["title"],
        "博主名": ["author_nickname", "nickname"],
        "博主主页": ["author_homepage_url"],
        "笔记链接": ["note_url"],
        "点赞量": ["liked_count"],
        "收藏量": ["collected_count"],
        "评论量": ["comment_count"],
        "分享量": ["share_count"],
        "搜索关键词": ["source_keyword"],
        "author_nickname": ["nickname"],
        "author_user_id": ["user_id"],
        "comment_user_id": ["user_id"],
        "comment_user_nickname": ["nickname"],
    }
    numeric_fields = {
        "liked_count", "collected_count", "comment_count", "share_count", "like_count", "create_time",
        "点赞量", "收藏量", "评论量", "分享量",
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
        if field_name == "author_homepage_url" and value == "":
            author_id = row.get("author_user_id") or row.get("user_id")
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
    cmd = [
        lark_cli_bin, "base", "+record-list",
        "--base-token", base_token,
        "--table-id", table_id,
        "--limit", "200",
    ]
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
            check=False,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail="未找到 lark-cli，请先安装并完成授权")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="读取规则表超时（20s）")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"读取规则表异常: {e}")

    if result.returncode != 0:
        err_msg = result.stderr or result.stdout
        raise HTTPException(status_code=400, detail=f"读取规则表失败: {err_msg[:400]}")

    try:
        payload = json.loads(result.stdout)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"规则表返回解析失败: {e}")

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


@router.post("/start")
async def start_crawler(request: CrawlerStartRequest):
    """Start crawler task"""
    success = await crawler_manager.start(request)
    if not success:
        # Handle concurrent/duplicate requests: if process is already running, return 400 instead of 500
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")

    return {"status": "ok", "message": "Crawler started successfully"}


@router.post("/sync-local-to-base")
async def sync_local_to_base(request: LocalToBaseSyncRequest):
    """Sync latest local jsonl file to Feishu Base, only keeping target table fields."""
    table_fields = await _read_table_fields(request.base_token, request.table_id)
    if not table_fields:
        raise HTTPException(status_code=400, detail="目标数据表没有可用字段")

    file_path = Path(request.file_path) if request.file_path else _latest_local_file(request.data_type)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"本地文件不存在: {file_path}")

    rows: List[List[Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if request.source_keyword and str(obj.get("source_keyword", "")) != request.source_keyword:
                continue
            rows.append(_row_to_table_values(obj, table_fields, request.data_type))

    if request.data_type == "notes":
        liked_idx = table_fields.index("liked_count") if "liked_count" in table_fields else -1
        if liked_idx >= 0:
            rows.sort(key=lambda x: int(x[liked_idx]) if isinstance(x[liked_idx], int) else 0, reverse=True)
    if request.limit and request.limit > 0:
        rows = rows[:request.limit]
    if not rows:
        raise HTTPException(status_code=400, detail="未找到可同步的数据（请检查关键词/文件类型）")

    created = 0
    batch_size = 200
    lark_cli_bin = shutil.which("lark-cli") or shutil.which("lark-cli.cmd") or "lark-cli"
    for i in range(0, len(rows), batch_size):
        payload = {"fields": table_fields, "rows": rows[i:i + batch_size]}
        await _run_lark_cli(
            [
                lark_cli_bin, "base", "+record-batch-create",
                "--base-token", request.base_token,
                "--table-id", request.table_id,
                "--json", json.dumps(payload, ensure_ascii=False),
            ],
            timeout_sec=60,
        )
        created += len(payload["rows"])

    return {
        "status": "ok",
        "message": "Local data synced to Base",
        "file": str(file_path),
        "fields": table_fields,
        "created": created,
    }


@router.post("/start-from-rule")
async def start_crawler_from_rule(request: RuleTableStartRequest):
    """Read one enabled rule from Lark Base and start crawler automatically."""
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
        platform=request.platform,
        login_type=request.login_type,
        crawler_type=request.crawler_type,
        keywords=str(selected.get("关键词", "") or ""),
        start_page=request.start_page,
        max_notes_count=request.max_notes_count,
        enable_comments=request.enable_comments,
        enable_sub_comments=request.enable_sub_comments,
        save_option=request.save_option,
        cookies=request.cookies,
        headless=request.headless,
        xhs_sort_by=str(selected.get("排序", "综合") or "综合"),
        xhs_note_type=str(selected.get("笔记类型", "不限") or "不限"),
        xhs_publish_time=str(selected.get("发布时间", "不限") or "不限"),
        xhs_search_scope=str(selected.get("搜索范围", "不限") or "不限"),
        xhs_location=str(selected.get("位置距离", "不限") or "不限"),
    )
    if not start_request.keywords:
        raise HTTPException(status_code=400, detail="规则缺少关键词，无法启动")

    success = await crawler_manager.start(start_request)
    if not success:
        if crawler_manager.process and crawler_manager.process.poll() is None:
            raise HTTPException(status_code=400, detail="Crawler is already running")
        raise HTTPException(status_code=500, detail="Failed to start crawler")

    return {
        "status": "ok",
        "message": "Crawler started from rule successfully",
        "rule_used": {
            "规则名称": selected.get("规则名称"),
            "关键词": selected.get("关键词"),
            "笔记类型": selected.get("笔记类型"),
            "发布时间": selected.get("发布时间"),
            "排序": selected.get("排序"),
        }
    }


@router.post("/stop")
async def stop_crawler():
    """Stop crawler task"""
    success = await crawler_manager.stop()
    if not success:
        # Handle concurrent/duplicate requests: if process already exited/doesn't exist, return 400 instead of 500
        if not crawler_manager.process or crawler_manager.process.poll() is not None:
            raise HTTPException(status_code=400, detail="No crawler is running")
        raise HTTPException(status_code=500, detail="Failed to stop crawler")

    return {"status": "ok", "message": "Crawler stopped successfully"}


@router.get("/status", response_model=CrawlerStatusResponse)
async def get_crawler_status():
    """Get crawler status"""
    return crawler_manager.get_status()


@router.get("/logs")
async def get_logs(limit: int = 100):
    """Get recent logs"""
    logs = crawler_manager.logs[-limit:] if limit > 0 else crawler_manager.logs
    return {"logs": [log.model_dump() for log in logs]}
