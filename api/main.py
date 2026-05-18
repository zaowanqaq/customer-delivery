# -*- coding: utf-8 -*-
"""
MediaCrawler WebUI API Server
Start command: uvicorn api.main:app --port 8080 --reload
Or: python -m api.main
"""
import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .routers import crawler_router, data_router, notes_router, websocket_router

app = FastAPI(
    title="MediaCrawler WebUI API",
    description="API for controlling MediaCrawler from WebUI",
    version="1.0.0"
)

# Get webui static files directory
WEBUI_DIR = os.path.join(os.path.dirname(__file__), "webui")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OPS_CONFIG_PATH = PROJECT_ROOT / "config" / "ops_config.json"

OPS_CONFIG_DEFAULT = {
    "platform": "xhs",
    "login_type": "qrcode",
    "crawler_type": "search",
    "keywords": "AI运营",
    "start_page": 1,
    "max_notes_count": 20,
    "max_comments_count_singlenotes": 10,
    "enable_comments": True,
    "enable_sub_comments": False,
    "enable_media": False,
    "save_option": "csv",
    "cookies": "",
    "headless": False,
    "xhs_sort_by": "综合",
    "xhs_note_type": "不限",
    "xhs_publish_time": "不限",
    "xhs_search_scope": "不限",
    "xhs_location": "不限",
    "rule_base_token": "",
    "rule_table_id": "",
    "rule_name": "",
    "template_base_token": "",
    "sync_base_token": "",
    "account_filter_table_id": "",
    "sync_notes_table_id": "",
    "sync_comments_table_id": "",
    "sync_limit": 0,
    "sample_creator_ids": "",
    "notes_per_creator": 20,
    "scenario_base_token": "",
    "account_filter_table_name": "账号筛选表",
    "viral_monitor_table_name": "爆款监控表",
    "note_recreation_table_name": "笔记二创表",
    "comments_table_name": "评论表",
    "collaboration_monitor_table_name": "合作笔记监控表",
    "collab_project_name": "",
    "collab_source_keyword": "",
    "collab_creator_ids": "",
    "collab_notes_per_creator": 20,
    "note_recreation_table_id": "",
    "collab_table_id": "",
    "collab_interval_hours": 4,
    "collab_sync_limit": 20,
    "project_name": "",
    "current_project_key": "",
    "project_profiles": {},
}

PROJECT_BOUND_FIELDS = {
    "project_name",
    "template_base_token",
    "sync_base_token",
    "account_filter_table_id",
    "sync_notes_table_id",
    "note_recreation_table_id",
    "sync_comments_table_id",
    "collab_table_id",
    "keywords",
    "xhs_sort_by",
    "xhs_note_type",
    "xhs_publish_time",
    "max_notes_count",
    "max_comments_count_singlenotes",
    "sample_creator_ids",
    "notes_per_creator",
    "collab_creator_ids",
    "collab_notes_per_creator",
    "collab_interval_hours",
    "collab_sync_limit",
}


class OpsConfigPayload(BaseModel):
    platform: str = "xhs"
    login_type: str = "qrcode"
    crawler_type: str = "search"
    keywords: str = ""
    start_page: int = 1
    max_notes_count: int = 20
    max_comments_count_singlenotes: int = 10
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = False
    save_option: str = "csv"
    cookies: str = ""
    headless: bool = False
    xhs_sort_by: str = "综合"
    xhs_note_type: str = "不限"
    xhs_publish_time: str = "不限"
    xhs_search_scope: str = "不限"
    xhs_location: str = "不限"
    rule_base_token: str = ""
    rule_table_id: str = ""
    rule_name: str = ""
    template_base_token: str = ""
    sync_base_token: str = ""
    account_filter_table_id: str = ""
    sync_notes_table_id: str = ""
    sync_comments_table_id: str = ""
    sync_limit: int = 0
    sample_creator_ids: str = ""
    notes_per_creator: int = 20
    scenario_base_token: str = ""
    account_filter_table_name: str = "账号筛选表"
    viral_monitor_table_name: str = "爆款监控表"
    note_recreation_table_name: str = "笔记二创表"
    comments_table_name: str = "评论表"
    collaboration_monitor_table_name: str = "合作笔记监控表"
    collab_project_name: str = ""
    collab_source_keyword: str = ""
    collab_creator_ids: str = ""
    collab_notes_per_creator: int = 20
    note_recreation_table_id: str = ""
    collab_table_id: str = ""
    collab_interval_hours: int = 4
    collab_sync_limit: int = 20
    project_name: str = ""
    current_project_key: str = ""
    project_profiles: Dict[str, Dict[str, Any]] = {}


def _load_ops_config() -> dict:
    config = dict(OPS_CONFIG_DEFAULT)
    if OPS_CONFIG_PATH.exists():
        try:
            with open(OPS_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update(loaded)
        except Exception:
            pass
    return config


def _save_ops_config(config_data: dict) -> None:
    OPS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OPS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

# CORS configuration - allow frontend dev server access
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # Vite dev server
        "http://localhost:3000",  # Backup port
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(crawler_router, prefix="/api")
app.include_router(data_router, prefix="/api")
app.include_router(notes_router, prefix="/api")
app.include_router(websocket_router, prefix="/api")


@app.get("/")
async def serve_frontend():
    """Return frontend page"""
    index_path = os.path.join(WEBUI_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {
        "message": "MediaCrawler WebUI API",
        "version": "1.0.0",
        "docs": "/docs",
        "note": "WebUI not found, please build it first: cd webui && npm run build"
    }


@app.get("/ops-config")
async def serve_ops_config():
    """Return independent ops config page."""
    page_path = os.path.join(WEBUI_DIR, "ops_config.html")
    if os.path.exists(page_path):
        return FileResponse(page_path)
    return {"message": "ops_config.html not found", "path": page_path}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/env/check")
async def check_environment():
    """Check if MediaCrawler environment is configured correctly"""
    try:
        # Run uv run main.py --help command to check environment
        process = await asyncio.create_subprocess_exec(
            "uv", "run", "main.py", "--help",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd="."  # Project root directory
        )
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=30.0  # 30 seconds timeout
        )

        if process.returncode == 0:
            return {
                "success": True,
                "message": "MediaCrawler environment configured correctly",
                "output": stdout.decode("utf-8", errors="ignore")[:500]  # Truncate to first 500 characters
            }
        else:
            error_msg = stderr.decode("utf-8", errors="ignore") or stdout.decode("utf-8", errors="ignore")
            return {
                "success": False,
                "message": "Environment check failed",
                "error": error_msg[:500]
            }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "message": "Environment check timeout",
            "error": "Command execution exceeded 30 seconds"
        }
    except FileNotFoundError:
        return {
            "success": False,
            "message": "uv command not found",
            "error": "Please ensure uv is installed and configured in system PATH"
        }
    except Exception as e:
        return {
            "success": False,
            "message": "Environment check error",
            "error": str(e)
        }


@app.get("/api/config/platforms")
async def get_platforms():
    """Get list of supported platforms"""
    return {
        "platforms": [
            {"value": "xhs", "label": "Xiaohongshu", "icon": "book-open"},
            {"value": "dy", "label": "Douyin", "icon": "music"},
            {"value": "ks", "label": "Kuaishou", "icon": "video"},
            {"value": "bili", "label": "Bilibili", "icon": "tv"},
            {"value": "wb", "label": "Weibo", "icon": "message-circle"},
            {"value": "tieba", "label": "Baidu Tieba", "icon": "messages-square"},
            {"value": "zhihu", "label": "Zhihu", "icon": "help-circle"},
        ]
    }


@app.get("/api/config/options")
async def get_config_options():
    """Get all configuration options"""
    return {
        "login_types": [
            {"value": "qrcode", "label": "QR Code Login"},
            {"value": "cookie", "label": "Cookie Login"},
        ],
        "crawler_types": [
            {"value": "search", "label": "Search Mode"},
            {"value": "detail", "label": "Detail Mode"},
            {"value": "creator", "label": "Creator Mode"},
        ],
        "save_options": [
            {"value": "jsonl", "label": "JSONL File"},
            {"value": "json", "label": "JSON File"},
            {"value": "csv", "label": "CSV File"},
            {"value": "excel", "label": "Excel File"},
            {"value": "sqlite", "label": "SQLite Database"},
            {"value": "db", "label": "MySQL Database"},
            {"value": "mongodb", "label": "MongoDB Database"},
        ],
        "xhs_search_filters": {
            "sort_by": ["综合", "最新", "最多点赞", "最多评论", "最多收藏"],
            "note_type": ["不限", "视频", "图文"],
            "publish_time": ["不限", "一天内", "一周内", "半年内"],
            "search_scope": ["不限", "已看过", "未看过", "已关注"],
            "location": ["不限", "同城", "附近"],
            "reserved_note": "search_scope/location are reserved in current API mode",
        },
    }


@app.get("/api/ops-config")
async def get_ops_config():
    """Get saved ops config for independent ops page."""
    return {"ok": True, "config": _load_ops_config()}


@app.post("/api/ops-config")
async def save_ops_config(payload: OpsConfigPayload):
    """Save ops config for independent ops page."""
    config_data = payload.model_dump()
    # Keep start_page >= 1 for stability.
    if config_data["start_page"] < 1:
        config_data["start_page"] = 1
    if config_data["max_notes_count"] < 1:
        config_data["max_notes_count"] = 1
    if config_data["max_comments_count_singlenotes"] < 1:
        config_data["max_comments_count_singlenotes"] = 1
    if config_data["sync_limit"] < 0:
        config_data["sync_limit"] = 0
    if config_data["notes_per_creator"] < 1:
        config_data["notes_per_creator"] = 1
    if config_data["collab_interval_hours"] not in (4, 8, 24):
        config_data["collab_interval_hours"] = 4
    if config_data["collab_sync_limit"] < 1:
        config_data["collab_sync_limit"] = 1
    if config_data["collab_notes_per_creator"] < 1:
        config_data["collab_notes_per_creator"] = 1
    if not isinstance(config_data.get("project_profiles"), dict):
        config_data["project_profiles"] = {}
    current_project_key = str(config_data.get("current_project_key") or "")
    current_profile = config_data["project_profiles"].get(current_project_key)
    if isinstance(current_profile, dict):
        for field in PROJECT_BOUND_FIELDS:
            if field in current_profile:
                config_data[field] = current_profile[field]
    _save_ops_config(config_data)
    return {"ok": True, "config": config_data}


# Mount static resources - must be placed after all routes
if os.path.exists(WEBUI_DIR):
    assets_dir = os.path.join(WEBUI_DIR, "assets")
    if os.path.exists(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    # Mount logos directory
    logos_dir = os.path.join(WEBUI_DIR, "logos")
    if os.path.exists(logos_dir):
        app.mount("/logos", StaticFiles(directory=logos_dir), name="logos")
    # Mount other static files (e.g., vite.svg)
    app.mount("/static", StaticFiles(directory=WEBUI_DIR), name="webui-static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)
