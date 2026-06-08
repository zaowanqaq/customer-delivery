# -*- coding: utf-8 -*-
import contextlib
import json
import asyncio
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import csv
import os
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config.runtime_paths import data_dir, downloads_dir
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
PGY_CDP_PORT = 9223
PGY_CDP_ENDPOINT = f"http://127.0.0.1:{PGY_CDP_PORT}"


def _pgy_cdp_available() -> bool:
    try:
        with urllib.request.urlopen(f"{PGY_CDP_ENDPOINT}/json/version", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


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
    cmd = [sys.executable, str(script_path), *final_args]
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

    max_retries = 3
    wait_seconds = 2
    last_err = ""
    _lark_env = None
    _node_bin = str(Path.home() / "nodejs" / "bin")
    if Path(_node_bin).is_dir():
        _lark_env = {**os.environ, "PATH": _node_bin + os.pathsep + os.environ.get("PATH", "")}
    for attempt in range(max_retries + 1):
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
            if _is_rate_limited(err_msg):
                raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {err_msg[:400]}")
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
        if _is_rate_limited(payload_text):
            raise HTTPException(status_code=429, detail=f"飞书接口限流（800004135），请稍后重试。原始信息: {payload_text[:400]}")
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
            json.dump(payload, tmp_file, ensure_ascii=False)
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


async def _read_table_fields(base_token: str, table_id: str) -> List[str]:
    payload = await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+field-list",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
        ],
        timeout_sec=30,
    )
    fields = payload.get("data", {}).get("fields", [])
    return [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]


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
    await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+field-create",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", json.dumps(field, ensure_ascii=False),
        ],
        timeout_sec=45,
    )


async def _ensure_creator_selection_fields(base_token: str, table_id: str) -> List[Dict[str, Any]]:
    existing = await _read_table_field_defs(base_token, table_id)
    existing_by_name = {field.get("name"): field for field in existing}
    wanted = _creator_selection_fields()
    for field in wanted:
        name = field["name"]
        if name not in existing_by_name:
            await _create_base_field(base_token, table_id, field)
    # Existing old tables may already have text fields named 截图/详情文本.
    refreshed = await _read_table_field_defs(base_token, table_id)
    refreshed_by_name = {field.get("name"): field for field in refreshed}
    for legacy_name, attachment_name in [("截图", "截图附件"), ("详情文本", "详情文本附件")]:
        legacy = refreshed_by_name.get(legacy_name)
        if legacy and legacy.get("type") != "attachment" and attachment_name not in refreshed_by_name:
            await _create_base_field(base_token, table_id, _attachment_field(attachment_name))
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


def _viral_monitor_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("博主名"),
        _text_field("项目名"),
        _datetime_field("采集时间"),
        _number_field("分享数"),
        _text_field("笔记链接"),
        _text_field("笔记类型"),
        _text_field("内容"),
        _number_field("评论数"),
        _text_field("封面图"),
        _text_field("已使用账号记录"),
        _datetime_field("首发时间"),
        _text_field("关键词"),
        _number_field("收藏数"),
        _number_field("点赞数"),
        _text_field("标题"),
        _text_field("博主主页"),
        _text_field("当日使用标记"),
        _number_field("博主粉丝数"),
        _text_field("笔记ID"),
        _text_field("话题标签"),
    ]


def _comments_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("项目名"),
        _text_field("关键词"),
        _text_field("笔记ID"),
        _text_field("评论内容"),
        _text_field("评论用户"),
        _datetime_field("评论时间"),
        _text_field("IP属地"),
        _number_field("点赞数"),
        _number_field("二级评论数"),
        _text_field("父评论ID"),
        _text_field("评论图片"),
    ]


def _creator_selection_fields() -> List[Dict[str, Any]]:
    return [
        _text_field("类型"),
        _text_field("去重键"),
        _number_field("排名"),
        _text_field("达人昵称"),
        _text_field("小红书号"),
        _text_field("目标达人昵称"),
        _text_field("目标小红书号"),
        _text_field("地区"),
        _number_field("粉丝数"),
        _number_field("获赞收藏"),
        _number_field("商业笔记数"),
        _number_field("最低报价"),
        _number_field("图文报价"),
        _number_field("视频报价"),
        _text_field("标签"),
        _text_field("博主优势"),
        _text_field("数据日期"),
        _number_field("发布笔记数"),
        _number_field("曝光中位数"),
        _number_field("阅读中位数"),
        _number_field("互动中位数"),
        _number_field("中位点赞量"),
        _number_field("中位收藏量"),
        _number_field("中位评论量"),
        _number_field("中位分享量"),
        _number_field("中位关注量"),
        _text_field("互动率"),
        _text_field("视频完播率"),
        _text_field("图文3秒阅读率"),
        _text_field("千赞笔记比例"),
        _text_field("百赞笔记比例"),
        _number_field("近7日活跃天数"),
        _number_field("邀约数"),
        _text_field("响应率"),
        _number_field("粉丝增量"),
        _text_field("粉丝增长率"),
        _text_field("活跃粉丝占比"),
        _text_field("阅读粉丝占比"),
        _text_field("互动粉丝占比"),
        _text_field("付费粉丝占比"),
        _text_field("女性粉丝占比"),
        _text_field("男性粉丝占比"),
        _text_field("主要年龄段"),
        _text_field("省份TOP5"),
        _text_field("城市TOP5"),
        _text_field("兴趣TOP8"),
        _text_field("输出目录"),
        _attachment_field("截图"),
        _attachment_field("详情文本"),
        _text_field("更新时间"),
    ]


def _latest_local_file(data_type: str, crawler_type_hint: str = "") -> Path:
    project_root = Path(__file__).resolve().parents[2]
    suffix = "contents" if data_type == "notes" else "comments"
    data_roots = [data_dir() / "xhs"]
    legacy_data_root = project_root / "data" / "xhs"
    if legacy_data_root.resolve() != data_roots[0].resolve():
        data_roots.append(legacy_data_root)
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
    for data_root in data_roots:
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
        "账号名称": ["author_nickname", "nickname", "博主名", "账号"],
        "账号ID": ["author_user_id", "user_id", "author_id"],
        "小红书ID": ["author_user_id", "user_id", "author_id", "账号ID"],
        "账号主页": ["author_homepage_url", "author_profile_url", "博主主页"],
        "博主主页": ["author_homepage_url", "author_profile_url"],
        "主页链接": ["author_homepage_url", "author_profile_url", "博主主页", "账号主页"],
        "笔记链接": ["note_url"],
        "发布链接": ["note_url", "笔记链接"],
        "笔记ID": ["note_id", "id"],
        "关键词": ["source_keyword", "搜索关键词"],
        "搜索关键词": ["source_keyword"],
        "笔记类型": ["note_type", "type"],
        "封面图": ["image_list", "cover", "cover_url"],
        "话题标签": ["tag_list", "topics"],
        "点赞量": ["liked_count", "like_count", "点赞数"],
        "点赞数": ["liked_count", "like_count", "点赞量"],
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
        "阅读量": ["read_count", "view_count", "浏览量"],
        "发布日期": ["publish_date", "发布时间", "time", "create_time"],
        "博主粉丝数": ["author_fans", "author_fans_count", "fans_count"],
        "首发时间": ["time", "create_time", "发布时间"],
        "采集时间": ["last_update_time", "crawl_time", "抓取时间"],
        "评论ID": ["comment_id"],
        "评论内容": ["content"],
        "评论用户": ["comment_user_nickname", "nickname"],
        "评论用户ID": ["comment_user_id", "user_id"],
        "评论时间": ["create_time"],
        "IP属地": ["ip_location"],
        "二级评论数": ["sub_comment_count"],
        "父评论ID": ["parent_comment_id"],
        "评论图片": ["pictures"],
        "头像": ["avatar"],
        "author_nickname": ["nickname"], "author_user_id": ["user_id"], "comment_user_id": ["user_id"], "comment_user_nickname": ["nickname"],
    }
    datetime_fields = {"采集时间", "首发时间", "评论时间", "create_time", "last_modify_ts"}
    numeric_fields = {
        "liked_count", "collected_count", "comment_count", "share_count", "like_count",
        "点赞量", "收藏量", "评论量", "分享量",
        "点赞数", "收藏数", "评论数", "分享数",
        "点赞", "收藏", "评论", "分享", "阅读量", "互动总和", "发布日期",
        "博主粉丝数", "二级评论数",
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
                value = 0
        values.append(value)
    return values


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


def _pgy_row_to_values(row: Dict[str, Any], table_fields: List[str]) -> List[Any]:
    alias_map = {
        "类型": ["row_type", "类型"],
        "达人类型": ["row_type", "类型"],
        "去重键": ["dedupe_key", "去重键"],
        "排名": ["rank", "排名"],
        "达人昵称": ["nickname", "博主昵称", "博主名"],
        "博主昵称": ["nickname", "达人昵称", "博主名"],
        "博主名": ["nickname", "达人昵称", "博主昵称"],
        "小红书号": ["red_id", "小红书号"],
        "小红书ID": ["red_id", "小红书号"],
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
        "博主优势": ["kol_advantage", "博主优势"],
        "数据日期": ["data_date", "数据日期"],
        "发布笔记数": ["note_number", "发布笔记数"],
        "曝光中位数": ["imp_median", "曝光中位数"],
        "阅读中位数": ["read_median", "阅读中位数"],
        "互动中位数": ["interaction_median", "互动中位数"],
        "中位点赞量": ["like_median", "中位点赞量"],
        "中位收藏量": ["collect_median", "中位收藏量"],
        "中位评论量": ["comment_median", "中位评论量"],
        "中位分享量": ["share_median", "中位分享量"],
        "中位关注量": ["follow_median", "中位关注量"],
        "互动率": ["interaction_rate", "互动率"],
        "视频完播率": ["video_full_view_rate", "视频完播率"],
        "图文3秒阅读率": ["picture_3s_view_rate", "图文3秒阅读率"],
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
        "采集时间": ["updated_at", "采集时间"],
    }
    numeric_fields = {"排名", "粉丝数", "获赞收藏", "商业笔记数", "最低报价", "图文报价", "视频报价", "发布笔记数", "曝光中位数", "阅读中位数", "互动中位数", "中位点赞量", "中位收藏量", "中位评论量", "中位分享量", "中位关注量", "近7日活跃天数", "邀约数", "粉丝增量"}
    values: List[Any] = []
    for field_name in table_fields:
        keys = alias_map.get(field_name, [field_name])
        value = ""
        for key in keys:
            if key in row and row[key] not in ("", None):
                value = row[key]
                break
        if field_name in numeric_fields and value not in ("", None):
            try:
                value = float(str(value).replace(",", ""))
                if value.is_integer():
                    value = int(value)
            except Exception:
                pass
        values.append(value)
    return values


def _pgy_dedupe_key(row: Dict[str, Any]) -> str:
    row_type = str(row.get("row_type") or row.get("类型") or "").strip() or "未知"
    target = str(
        row.get("target_red_id")
        or row.get("目标小红书号")
        or row.get("target_nickname")
        or row.get("目标达人昵称")
        or ""
    ).strip()
    identity = str(
        row.get("red_id")
        or row.get("小红书号")
        or row.get("nickname")
        or row.get("达人昵称")
        or ""
    ).strip()
    return " :: ".join([row_type, target, identity]).lower()


def _pgy_row_to_record(row: Dict[str, Any], table_fields: List[str]) -> Dict[str, Any]:
    values = _pgy_row_to_values(row, table_fields)
    return {
        field_name: value
        for field_name, value in zip(table_fields, values)
        if value not in ("", None)
    }


async def _read_existing_pgy_records(base_token: str, table_id: str, field_names: List[str]) -> Dict[str, str]:
    lark_cli_bin = _find_lark_cli()
    existing: Dict[str, str] = {}
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
                "row_type": existing_row.get("类型"),
                "target_red_id": existing_row.get("目标小红书号"),
                "target_nickname": existing_row.get("目标达人昵称"),
                "red_id": existing_row.get("小红书号"),
                "nickname": existing_row.get("达人昵称"),
            })
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
    fans = summary.get("fan_analysis") or {}
    fans_summary = fans.get("fans_summary") or {}
    target_metrics = summary.get("target_metrics") or {}
    if not target_metrics:
        data_summary = propagation.get("data_summary") or {}
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
            "kol_advantage": data_summary.get("kolAdvantage") or "",
            "data_date": data_summary.get("dateKey") or fans_profile.get("dateKey") or "",
            "note_number": data_summary.get("noteNumber") or notes_rate.get("noteNumber") or "",
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
        row = {
            "row_type": "相似博主",
            "target_nickname": target_row["nickname"],
            "target_red_id": target_row["red_id"],
            "output_dir": output_dir,
            "screenshot": item.get("screenshot") or "",
            "detail_text": item.get("detail_text") or "",
            "updated_at": updated_at,
            **item,
        }
        row["dedupe_key"] = _pgy_dedupe_key(row)
        rows.append(row)
    return rows


async def _upload_base_attachment(base_token: str, table_id: str, record_id: str, field_name: str, file_path: str) -> None:
    if not file_path:
        return
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    path = path.resolve()
    if not path.exists() or not path.is_file():
        return
    project_root = Path(__file__).resolve().parents[2]
    try:
        cli_file = str(path.relative_to(project_root))
    except ValueError:
        cli_file = str(path)
    await _run_lark_cli(
        [
            _find_lark_cli(),
            "base", "+record-upload-attachment",
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--record-id", record_id,
            "--field-id", field_name,
            "--file", cli_file,
            "--name", path.name,
        ],
        timeout_sec=60,
    )


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
    table_fields = [field.get("name") for field in field_defs if field.get("name")]
    field_types = {field.get("name"): field.get("type") for field in field_defs}
    writable_fields = [name for name in table_fields if field_types.get(name) != "attachment"]
    output_dir = str(summary_path.parent)
    rows = _pgy_summary_to_rows(summary, output_dir)
    dedupe_fields = [name for name in ["去重键", "类型", "达人昵称", "小红书号", "目标达人昵称", "目标小红书号"] if name in table_fields]
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
        record_id = existing_records.get(key)
        if record_id:
            payload = _pgy_row_to_record(row, writable_fields)
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
            updated += 1
        else:
            new_rows.append(row)
            seen_new_keys.add(key)

    table_rows = [_pgy_row_to_values(row, writable_fields) for row in new_rows]
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
    screenshot_field = "截图" if field_types.get("截图") == "attachment" else "截图附件"
    attachment_uploads = 0
    attachment_errors: List[str] = []
    for record_id, row in zip(record_ids, rows_for_attachments):
        if field_types.get(screenshot_field) == "attachment" and row.get("screenshot"):
            try:
                await _upload_base_attachment(request.base_token, request.table_id, record_id, screenshot_field, row.get("screenshot") or "")
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
        enable_media=request.enable_media, save_option=request.save_option, cookies=request.cookies, headless=request.headless,
    )
    _clear_creator_data_files()
    success = await crawler_manager.start(start_request)
    if not success:
        raise HTTPException(status_code=500, detail="合作监控抓取任务启动失败")
    if not await _wait_crawler_idle(timeout_sec=1800):
        raise HTTPException(status_code=504, detail="合作监控抓取超时（30分钟）")


def _clear_creator_data_files() -> None:
    data_roots = [data_dir() / "xhs"]
    project_root = Path(__file__).resolve().parents[2]
    legacy_data_root = project_root / "data" / "xhs"
    if legacy_data_root.resolve() != data_roots[0].resolve():
        data_roots.append(legacy_data_root)
    for data_root in data_roots:
        for suffix_dir in ("csv", "jsonl", "json"):
            dir_path = data_root / suffix_dir
            if not dir_path.exists():
                continue
            for f in dir_path.glob("creator_contents_*.*"):
                with contextlib.suppress(Exception):
                    f.unlink()


async def _sync_collaboration_snapshot(request: CollaborationMonitorStartRequest, monitor_tag: str) -> Dict[str, Any]:
    table_fields = await _read_table_fields(request.base_token, request.table_id)
    if not table_fields:
        raise HTTPException(status_code=400, detail="合作监控表没有可用字段")
    file_path = Path(request.file_path) if request.file_path else _latest_local_file("notes", "creator")
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
    lark_cli_bin = _find_lark_cli()
    created = 0
    for i in range(0, len(rows), 200):
        payload = {"fields": table_fields, "rows": rows[i:i + 200]}
        with _lark_json_arg(payload) as json_arg:
            await _run_lark_cli(
                [lark_cli_bin, "base", "+record-batch-create", "--as", "user", "--base-token", request.base_token, "--table-id", request.table_id, "--json", json_arg],
                timeout_sec=60,
            )
        created += len(payload["rows"])
    return {"created": created, "file": str(file_path)}


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
        project_root = Path(__file__).resolve().parents[2]
        script_path = project_root / "tools" / "pgy_automation.py"
        if _pgy_cdp_available():
            return {
                "status": "login_window_opened",
                "message": "蒲公英登录窗口已打开",
                "cdp": PGY_CDP_ENDPOINT,
                "wait_seconds": wait_seconds,
            }
        cmd = [
            sys.executable,
            str(script_path),
            *args,
            "--remote-debugging-port",
            str(PGY_CDP_PORT),
            "--detach-hold-open",
        ]
        try:
            subprocess.Popen(
                cmd,
                cwd=str(project_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"蒲公英登录窗口启动失败: {exc}") from exc
        return {
            "status": "login_window_opened",
            "message": "蒲公英登录窗口已打开，请在浏览器内完成登录；登录状态每 1 秒检测一次",
            "cdp": PGY_CDP_ENDPOINT,
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
    account_filter_fields = [_text_field("项目名"), _text_field("搜索关键词"), _text_field("账号"), _text_field("账号ID"), _text_field("账号主页"), _text_field("笔记标题"), _text_field("笔记链接"), _number_field("点赞量"), _number_field("评论量"), _number_field("收藏量"), _text_field("语义标签"), _text_field("推荐理由")]
    viral_monitor_fields = _viral_monitor_fields()
    note_recreation_fields = _viral_monitor_fields()
    comments_fields = _comments_fields()
    collaboration_fields = [_text_field("项目名"), _text_field("监控周期"), _text_field("搜索关键词"), _text_field("笔记标题"), _text_field("笔记链接"), _text_field("博主名"), _number_field("点赞量"), _number_field("评论量"), _number_field("收藏量"), _number_field("分享量"), _datetime_field("抓取时间")]
    creator_selection_fields = _creator_selection_fields()
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
        await create_or_reuse(request.note_recreation_table_name, note_recreation_fields),
        await create_or_reuse(request.comments_table_name, comments_fields),
        await create_or_reuse(request.collaboration_monitor_table_name, collaboration_fields),
        await create_or_reuse(request.creator_selection_table_name, creator_selection_fields),
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
                creator_selection_table_name=request.creator_selection_table_name,
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
        if request.source_keyword and not (obj.get("source_keyword") or obj.get("关键词")):
            obj["source_keyword"] = request.source_keyword
            obj["关键词"] = request.source_keyword
        rows.append(_row_to_table_values(obj, table_fields, request.data_type))
    if request.data_type == "notes":
        liked_idx = table_fields.index("liked_count") if "liked_count" in table_fields else -1
        if liked_idx >= 0:
            rows.sort(key=lambda x: int(x[liked_idx]) if isinstance(x[liked_idx], int) else 0, reverse=True)
    if request.limit and request.limit > 0:
        rows = rows[:request.limit]
    if not rows:
        raise HTTPException(status_code=400, detail="未找到可同步的数据（请检查关键词/文件类型）")
    lark_cli_bin = _find_lark_cli()
    created = 0
    for i in range(0, len(rows), 200):
        payload = {"fields": table_fields, "rows": rows[i:i + 200]}
        with _lark_json_arg(payload) as json_arg:
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
                    json_arg,
                ],
                timeout_sec=60,
            )
        created += len(payload["rows"])
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
    }


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
