# -*- coding: utf-8 -*-
"""
MediaCrawler WebUI API Server
Start command: uvicorn api.main:app --port 8081
Or: python -m api.main
"""
import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import config
from config.runtime_paths import ensure_runtime_dirs, ops_config_path
from tools.browser_launcher import BrowserLauncher
from .routers import crawler_router, data_router, notes_router, websocket_router

app = FastAPI(
    title="MediaCrawler WebUI API",
    description="API for controlling MediaCrawler from WebUI",
    version="1.0.0"
)

# Get webui static files directory
WEBUI_DIR = os.path.join(os.path.dirname(__file__), "webui")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OPS_CONFIG_PATH = ops_config_path()
LEGACY_OPS_CONFIG_PATH = PROJECT_ROOT / "config" / "ops_config.json"
ensure_runtime_dirs()

OPS_CONFIG_DEFAULT = {
    "platform": "xhs",
    "login_type": "cookie",
    "crawler_type": "search",
    "keywords": "",
    "start_page": 1,
    "max_notes_count": 20,
    "max_comments_count_singlenotes": 10,
    "enable_comments": True,
    "enable_sub_comments": False,
    "enable_media": True,
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
    "folder_token": "",
    "template_base_token": "",
    "sync_base_token": "",
    "account_filter_table_id": "",
    "sync_notes_table_id": "",
    "sync_comments_table_id": "",
    "sync_limit": 0,
    "sync_file_path": "",
    "sample_creator_ids": "",
    "notes_per_creator": 20,
    "scenario_base_token": "",
    "account_filter_table_name": "账号筛选表",
    "viral_monitor_table_name": "爆款监控表",
    "note_recreation_table_name": "笔记二创表",
    "comments_table_name": "笔记评论表",
    "collaboration_monitor_table_name": "合作笔记监控表",
    "collab_comments_table_name": "合作笔记评论表",
    "creator_selection_table_name": "达人智能圈选表",
    "collab_project_name": "",
    "collab_source_keyword": "",
    "collab_creator_ids": "",
    "collab_notes_per_creator": 20,
    "note_recreation_table_id": "",
    "collab_table_id": "",
    "collab_comments_table_id": "",
    "collab_interval_hours": 4,
    "collab_sync_limit": 20,
    "pgy_nickname": "",
    "pgy_red_id": "",
    "pgy_table_id": "",
    "pgy_sync_after_run": "true",
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
    "collab_comments_table_id",
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
    "pgy_table_id",
    "pgy_sync_after_run",
}

REQUIRED_RUNTIME_IMPORTS = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "playwright": "playwright",
    "pandas": "pandas",
    "openpyxl": "openpyxl",
    "websockets": "websockets",
    "xhshow": "xhshow",
    "cv2": "opencv-python",
}


class OpsConfigPayload(BaseModel):
    platform: str = "xhs"
    login_type: str = "cookie"
    crawler_type: str = "search"
    keywords: str = ""
    start_page: int = 1
    max_notes_count: int = 20
    max_comments_count_singlenotes: int = 10
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = True
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
    folder_token: str = ""
    template_base_token: str = ""
    sync_base_token: str = ""
    account_filter_table_id: str = ""
    sync_notes_table_id: str = ""
    sync_comments_table_id: str = ""
    sync_limit: int = 0
    sync_file_path: str = ""
    sample_creator_ids: str = ""
    notes_per_creator: int = 20
    scenario_base_token: str = ""
    account_filter_table_name: str = "账号筛选表"
    viral_monitor_table_name: str = "爆款监控表"
    note_recreation_table_name: str = "笔记二创表"
    comments_table_name: str = "笔记评论表"
    collaboration_monitor_table_name: str = "合作笔记监控表"
    collab_comments_table_name: str = "合作笔记评论表"
    creator_selection_table_name: str = "达人智能圈选表"
    collab_project_name: str = ""
    collab_source_keyword: str = ""
    collab_creator_ids: str = ""
    collab_notes_per_creator: int = 20
    note_recreation_table_id: str = ""
    collab_table_id: str = ""
    collab_comments_table_id: str = ""
    collab_interval_hours: int = 4
    collab_sync_limit: int = 20
    pgy_nickname: str = ""
    pgy_red_id: str = ""
    pgy_table_id: str = ""
    pgy_sync_after_run: str = "true"
    project_name: str = ""
    current_project_key: str = ""
    project_profiles: Dict[str, Dict[str, Any]] = {}


def _load_ops_config() -> dict:
    config = dict(OPS_CONFIG_DEFAULT)
    source_path = OPS_CONFIG_PATH if OPS_CONFIG_PATH.exists() else LEGACY_OPS_CONFIG_PATH
    if source_path.exists():
        try:
            with open(source_path, "r", encoding="utf-8") as f:
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


def _missing_runtime_dependencies() -> list[str]:
    missing = []
    for module_name, package_name in REQUIRED_RUNTIME_IMPORTS.items():
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(package_name)
    return missing


def _check_cdp_browser_configuration() -> tuple[bool, str, str, bool]:
    if not config.ENABLE_CDP_MODE:
        return True, "CDP 模式未启用", "", False

    custom_path = (config.CUSTOM_BROWSER_PATH or "").strip()
    if custom_path:
        custom_browser = Path(custom_path).expanduser()
        ok = custom_browser.is_file()
        message = "已找到自定义浏览器" if ok else "CUSTOM_BROWSER_PATH 指向的浏览器不存在"
        return ok, message, str(custom_browser), True

    browser_paths = BrowserLauncher().detect_browser_paths()
    if browser_paths:
        return True, "已找到系统 Chrome/Edge，可用于 CDP 模式", browser_paths[0], True

    return (
        False,
        "当前配置启用 ENABLE_CDP_MODE，但未找到系统 Chrome/Edge；请安装浏览器或设置 CUSTOM_BROWSER_PATH",
        f"CUSTOM_BROWSER_PATH={config.CUSTOM_BROWSER_PATH!r}",
        True,
    )

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


@app.get("/favicon.ico")
async def serve_favicon():
    """Return a favicon so browser checks do not produce a noisy 404."""
    favicon_path = os.path.join(WEBUI_DIR, "vite.svg")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return {"message": "favicon not found"}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/env/check")
async def check_environment():
    """Check whether the customer workstation can run the ops workbench."""
    checks = []

    def add_check(name: str, ok: bool, message: str, required: bool = True, detail: str = "") -> None:
        checks.append({
            "name": name,
            "ok": ok,
            "required": required,
            "message": message,
            "detail": detail[:500] if detail else "",
        })

    add_check(
        "Python",
        sys.version_info >= (3, 11),
        f"{sys.version.split()[0]}（要求 3.11 或更高）",
    )

    missing_packages = _missing_runtime_dependencies()
    if missing_packages:
        add_check(
            "Python 依赖",
            False,
            "依赖未安装完整，请重新运行启动脚本安装 requirements.txt",
            detail=", ".join(missing_packages),
        )
    else:
        add_check("Python 依赖", True, "运行期 Python 依赖已安装")

    try:
        script = (
            "from pathlib import Path; "
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); "
            "path=p.chromium.executable_path; "
            "p.stop(); "
            "print(path); "
            "raise SystemExit(0 if Path(path).exists() else 1)"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
        browser_path = stdout.decode("utf-8", errors="ignore").strip()
        err = stderr.decode("utf-8", errors="ignore").strip()
        add_check(
            "Playwright 浏览器",
            process.returncode == 0,
            "Chromium 已安装" if process.returncode == 0 else "Chromium 未安装，请运行 python -m playwright install chromium",
            detail=browser_path or err,
        )
    except Exception as exc:
        add_check("Playwright 浏览器", False, "Chromium 检查失败，请运行 python -m playwright install chromium", detail=str(exc))

    try:
        ok, message, detail, required = _check_cdp_browser_configuration()
        add_check("CDP 浏览器", ok, message, required=required, detail=detail)
    except Exception as exc:
        add_check(
            "CDP 浏览器",
            False,
            "CDP 浏览器配置检查失败；请确认 Chrome/Edge 安装或 CUSTOM_BROWSER_PATH 配置",
            detail=str(exc),
        )

    lark_bin = shutil.which("lark-cli") or shutil.which("lark-cli.cmd")
    if lark_bin:
        add_check("飞书 CLI", True, "已找到 lark-cli；请确认已用客户飞书账号完成授权", detail=lark_bin)
    else:
        add_check("飞书 CLI", False, "未找到 lark-cli；初始化 Base 和同步多维表前必须安装并授权")

    ensure_runtime_dirs()
    add_check("工作台配置目录", True, "已就绪", detail=str(OPS_CONFIG_PATH))
    add_check("运行数据目录", True, "已就绪", detail=str(OPS_CONFIG_PATH.parent.parent))

    success = all(item["ok"] for item in checks if item["required"])
    return {
        "success": success,
        "message": "环境检查通过" if success else "环境检查未通过，请按提示处理",
        "checks": checks,
    }


@app.get("/api/config/platforms")
async def get_platforms():
    """Get list of supported platforms."""
    return {
        "platforms": [
            {"value": "xhs", "label": "Xiaohongshu", "icon": "book-open"},
        ]
    }


@app.get("/api/config/options")
async def get_config_options():
    """Get all configuration options"""
    return {
        "login_types": [
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
    uvicorn.run(app, host="127.0.0.1", port=8081)
