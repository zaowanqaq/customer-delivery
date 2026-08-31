# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import base64
import asyncio
import io
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List
from urllib.parse import quote
from uuid import uuid4

import httpx

from api.schemas.creator_screening import CreatorCandidateInput
from api.services.local_ai_config import load_siliconflow_api_key
from config.runtime_paths import temp_dir


REQUIRED_CREATOR_COLUMNS = ("达人昵称", "博主ID", "主页链接", "达人价格")
DEFAULT_SILICONFLOW_KIMI_MODEL = "Pro/moonshotai/Kimi-K2.6"
SILICONFLOW_CHAT_COMPLETIONS_URL = "https://api.siliconflow.cn/v1/chat/completions"
SCREENING_PAGE_NAME_PREFIX = "mediacrawler_creator_screening"
DEFAULT_SCREENING_CONCURRENCY = 3
MAX_SCREENING_CONCURRENCY = 4
DEFAULT_AI_TIMEOUT_SECONDS = 90.0


def screening_concurrency() -> int:
    """Return the bounded number of independent profile-capture workers."""
    try:
        configured = int(os.getenv("CREATOR_SCREENING_CONCURRENCY", str(DEFAULT_SCREENING_CONCURRENCY)))
    except ValueError:
        configured = DEFAULT_SCREENING_CONCURRENCY
    return max(1, min(configured, MAX_SCREENING_CONCURRENCY))


def ai_timeout_seconds() -> float:
    try:
        configured = float(os.getenv("CREATOR_SCREENING_AI_TIMEOUT_SECONDS", str(DEFAULT_AI_TIMEOUT_SECONDS)))
    except ValueError:
        configured = DEFAULT_AI_TIMEOUT_SECONDS
    return max(30.0, min(configured, 300.0))


@dataclass
class CreatorImportResult:
    candidates: List[CreatorCandidateInput] = field(default_factory=list)
    invalid_rows: List[dict] = field(default_factory=list)


@dataclass
class RequirementRules:
    tags: List[str] = field(default_factory=list)
    raw_requirement: str = ""


@dataclass
class ProfileSnapshot:
    profile_url: str
    visible_text: str = ""
    ip_location: str = ""
    screenshot_paths: List[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""


@dataclass
class ScreeningDecision:
    status: str
    creator_type: str = ""
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)


@dataclass
class ScreeningResult:
    candidate: CreatorCandidateInput
    status: str
    profile_url: str
    ip_location: str = ""
    creator_type: str = ""
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)

    def to_row(self) -> dict:
        return {
            "序号": self.candidate.index,
            "是否符合筛选要求": self.status,
            "达人昵称": self.candidate.nickname,
            "博主id": self.candidate.blogger_id,
            "主页链接": self.profile_url,
            "ip地": self.ip_location,
            "达人类型": self.creator_type,
            "达人价格": self.candidate.price,
            "详情": {"理由": self.reason, "证据": self.evidence, "不确定项": self.uncertainties},
        }


@dataclass
class ScreeningJob:
    id: str
    requirement: str
    candidates: List[CreatorCandidateInput]
    results: List[ScreeningResult] = field(default_factory=list)
    completed: int = 0
    finished: bool = False
    task: asyncio.Task | None = field(default=None, repr=False)

    def to_payload(self) -> dict:
        exceptions = sum(1 for item in self.results if item.status == "异常")
        return {
            "job_id": self.id,
            "finished": self.finished,
            "progress": {
                "total": len(self.candidates),
                "completed": self.completed,
                "pending": max(0, len(self.candidates) - self.completed),
                "exceptions": exceptions,
            },
            "results": [item.to_row() for item in sorted(self.results, key=lambda item: item.candidate.index)],
        }


class CreatorScreeningAI:
    def __init__(self, siliconflow_key: str | None = None):
        # Tests may inject a key directly. The production router constructs this
        # with no arguments and reads only the WebUI-saved local configuration.
        self._injected_siliconflow_key = siliconflow_key
        self._siliconflow_key = ""
        self._siliconflow_kimi_model = DEFAULT_SILICONFLOW_KIMI_MODEL
        self._refresh_siliconflow_key()

    def _refresh_siliconflow_key(self) -> None:
        if self._injected_siliconflow_key is not None:
            self._siliconflow_key = self._injected_siliconflow_key.strip()
            return
        local_key = load_siliconflow_api_key()
        self._siliconflow_key = local_key

    def configuration_status(self) -> dict[str, bool | str]:
        self._refresh_siliconflow_key()
        return {
            "siliconflow_configured": bool(self._siliconflow_key),
            "active_provider": "SiliconFlow Kimi",
            "active_model": self._siliconflow_kimi_model,
            "configuration_source": "本机网页配置" if self._siliconflow_key else "",
        }

    @staticmethod
    def _model_error_detail(provider: str, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            message = ""
            raw_detail = ""
            try:
                payload = response.json()
                error = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(error, dict):
                    message = str(error.get("message") or "").strip()
                    metadata = error.get("metadata")
                    if isinstance(metadata, dict):
                        raw_detail = str(metadata.get("raw") or "").strip()
            except Exception:
                pass
            details = "；".join(part for part in (message, raw_detail) if part)
            return f"{provider} HTTP {response.status_code}" + (f"：{details[:280]}" if details else "")
        if isinstance(exc, httpx.RequestError):
            return f"{provider} 网络请求失败：{type(exc).__name__}"
        return f"{provider} 响应处理失败：{type(exc).__name__}"

    async def _post_json(self, url: str, headers: dict, body: dict) -> dict:
        async with httpx.AsyncClient(timeout=ai_timeout_seconds()) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("模型响应不是 JSON 对象")
        return payload

    @staticmethod
    def _chat_content(payload: dict) -> str:
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("模型响应缺少内容") from exc
        if not isinstance(content, str) or not content.strip():
            raise ValueError("模型响应内容为空")
        return content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    async def parse_requirement(self, requirement: str) -> RequirementRules:
        self._refresh_siliconflow_key()
        text = requirement.strip()
        if not text:
            raise ValueError("请填写筛选需求")
        messages = [
            {"role": "system", "content": "把用户的小红书达人筛选需求拆为简短、可展示的中文标签。仅返回 JSON：{\\\"tags\\\":[\\\"...\\\"]}。"},
            {"role": "user", "content": text},
        ]
        if not self._siliconflow_key:
            raise ValueError("请先在网页中配置 AI API Key")
        payload = await self._post_json(
            SILICONFLOW_CHAT_COMPLETIONS_URL,
            {"Authorization": "Bearer " + self._siliconflow_key, "Content-Type": "application/json"},
            {"model": self._siliconflow_kimi_model, "response_format": {"type": "json_object"}, "max_tokens": 200, "messages": messages},
        )
        decoded = json.loads(self._chat_content(payload))
        tags = decoded.get("tags") if isinstance(decoded, dict) else None
        if not isinstance(tags, list):
            raise ValueError("需求解析未返回 tags")
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        if not clean_tags:
            raise ValueError("需求解析没有可用标签")
        return RequirementRules(tags=clean_tags, raw_requirement=text)

    def _vision_message(self, rules: RequirementRules, snapshot: ProfileSnapshot) -> list[dict]:
        text = (
            "根据用户需求与小红书主页当前可见信息判断初筛结果。只可依据提供证据，不能推断实时位置、出行意愿或不可见历史内容。"
            "仅返回 JSON：{status,matched_tags,reason,evidence,uncertainties}。status 只能是 符合、不符合、待人工确认。"
            f"\\n需求标签：{'、'.join(rules.tags)}\\n原始需求：{rules.raw_requirement}"
            f"\\n主页链接：{snapshot.profile_url}\\n可见 IP：{snapshot.ip_location}\\n可见文字：{snapshot.visible_text[:6000]}"
        )
        content: list[dict] = [{"type": "text", "text": text}]
        for screenshot in snapshot.screenshot_paths[:2]:
            path = Path(screenshot)
            if not path.is_file():
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + encoded}})
        return [{"role": "user", "content": content}]

    async def evaluate(self, rules: RequirementRules, snapshot: ProfileSnapshot) -> ScreeningDecision:
        self._refresh_siliconflow_key()
        if snapshot.status == "待人工确认":
            return ScreeningDecision(status="待人工确认", reason=snapshot.error or "主页可见资料不足")
        if snapshot.status != "ok":
            return ScreeningDecision(status="异常", reason=snapshot.error or "主页快照失败")
        if not self._siliconflow_key:
            return ScreeningDecision(status="异常", reason="请先在网页中配置 AI API Key")
        try:
            payload = await self._post_json(
                SILICONFLOW_CHAT_COMPLETIONS_URL,
                {"Authorization": "Bearer " + self._siliconflow_key, "Content-Type": "application/json"},
                {"model": self._siliconflow_kimi_model, "max_tokens": 600, "messages": self._vision_message(rules, snapshot)},
            )
            return await self.to_decision(self._chat_content(payload), rules)
        except Exception as exc:
            return ScreeningDecision(status="异常", reason=f"AI 判定失败：{self._model_error_detail('SiliconFlow Kimi', exc)}")

    async def to_decision(self, content: str, rules: RequirementRules) -> ScreeningDecision:
        try:
            decoded: Any = json.loads(content)
            if not isinstance(decoded, dict):
                raise ValueError("响应不是对象")
            status = str(decoded.get("status", "")).strip()
            if status not in {"符合", "不符合", "待人工确认"}:
                raise ValueError("status 无效")
            matched = decoded.get("matched_tags", [])
            matched_tags = [str(item).strip() for item in matched if str(item).strip()] if isinstance(matched, list) else []
            creator_type = "｜".join(tag for tag in rules.tags if tag in matched_tags)
            evidence = decoded.get("evidence", [])
            uncertainties = decoded.get("uncertainties", [])
            evidence = [str(item).strip() for item in evidence if str(item).strip()] if isinstance(evidence, list) else []
            uncertainties = [str(item).strip() for item in uncertainties if str(item).strip()] if isinstance(uncertainties, list) else []
            if any("低置信" in item or "信息不足" in item for item in uncertainties):
                status = "待人工确认"
            return ScreeningDecision(
                status=status,
                creator_type=creator_type,
                reason=str(decoded.get("reason", "")).strip(),
                evidence=evidence,
                uncertainties=uncertainties,
            )
        except Exception:
            return ScreeningDecision(status="异常", reason="AI 返回格式无效")


def profile_url_for(candidate: CreatorCandidateInput) -> str:
    if candidate.profile_url:
        return candidate.profile_url
    return "https://www.xiaohongshu.com/user/profile/" + quote(candidate.blogger_id, safe="")


async def collect_profile_snapshot(candidate: CreatorCandidateInput, job_id: str, worker_index: int = 0) -> ProfileSnapshot:
    project_root = Path(__file__).resolve().parents[2]
    output_dir = temp_dir() / "creator_screening" / job_id / str(candidate.index)
    command = [
        sys.executable,
        str(project_root / "tools" / "xhs_profile_snapshot.py"),
        "--profile-url",
        profile_url_for(candidate),
        "--output-dir",
        str(output_dir),
        "--screening-page-name",
        f"{SCREENING_PAGE_NAME_PREFIX}_{worker_index + 1}",
    ]
    python_path = os.pathsep.join(filter(None, [str(project_root), os.environ.get("PYTHONPATH", "")]))
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(project_root),
            env={**os.environ, "PYTHONPATH": python_path},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=90)
    except Exception as exc:
        return ProfileSnapshot(profile_url=profile_url_for(candidate), status="异常", error=f"主页快照启动失败：{type(exc).__name__}")
    payload = None
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            payload = value
            break
    if process.returncode != 0 or not payload:
        return ProfileSnapshot(profile_url=profile_url_for(candidate), status="异常", error="主页快照没有返回有效结果")
    return ProfileSnapshot(
        profile_url=str(payload.get("profile_url") or profile_url_for(candidate)),
        visible_text=str(payload.get("visible_text") or ""),
        ip_location=str(payload.get("ip_location") or ""),
        screenshot_paths=[str(item) for item in payload.get("screenshot_paths", []) if str(item)],
        status=str(payload.get("status") or "异常"),
        error=str(payload.get("error") or ""),
    )


class CreatorScreeningJobManager:
    def __init__(self, ai: CreatorScreeningAI | None = None, snapshot_collector=collect_profile_snapshot):
        self._ai = ai or CreatorScreeningAI()
        self._snapshot_collector = snapshot_collector
        self._jobs: dict[str, ScreeningJob] = {}

    async def start(self, requirement: str, candidates: List[CreatorCandidateInput]) -> ScreeningJob:
        job = ScreeningJob(id=uuid4().hex, requirement=requirement, candidates=candidates)
        self._jobs[job.id] = job
        job.task = asyncio.create_task(self._run(job))
        return job

    def get(self, job_id: str) -> ScreeningJob | None:
        return self._jobs.get(job_id)

    async def _run(self, job: ScreeningJob) -> None:
        try:
            rules = await self._ai.parse_requirement(job.requirement)
        except Exception as exc:
            for candidate in job.candidates:
                job.results.append(ScreeningResult(candidate, "异常", profile_url_for(candidate), reason=f"需求解析失败：{type(exc).__name__}"))
                job.completed += 1
            job.finished = True
            return
        next_candidate = 0
        next_candidate_lock = asyncio.Lock()

        async def process_candidate(candidate: CreatorCandidateInput, worker_index: int) -> None:
            try:
                snapshot = await self._snapshot_collector(candidate, job.id, worker_index)
                decision = await self._ai.evaluate(rules, snapshot)
                job.results.append(
                    ScreeningResult(
                        candidate=candidate,
                        status=decision.status,
                        profile_url=snapshot.profile_url or profile_url_for(candidate),
                        ip_location=snapshot.ip_location,
                        creator_type=decision.creator_type,
                        reason=decision.reason,
                        evidence=decision.evidence,
                        uncertainties=decision.uncertainties,
                    )
                )
            except Exception as exc:
                job.results.append(ScreeningResult(candidate, "异常", profile_url_for(candidate), reason=f"初筛失败：{type(exc).__name__}"))
            job.completed += 1

        async def worker(worker_index: int) -> None:
            nonlocal next_candidate
            while True:
                async with next_candidate_lock:
                    if next_candidate >= len(job.candidates):
                        return
                    candidate = job.candidates[next_candidate]
                    next_candidate += 1
                await process_candidate(candidate, worker_index)

        worker_count = min(screening_concurrency(), len(job.candidates))
        if worker_count:
            await asyncio.gather(*(worker(worker_index) for worker_index in range(worker_count)))
        job.finished = True


def _read_csv_rows(content: bytes) -> List[dict]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        return list(csv.DictReader(io.StringIO(text)))
    raise ValueError("CSV 文件编码无法识别，请使用 UTF-8 或 GB18030") from last_error


def _read_excel_rows(content: bytes) -> List[dict]:
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise ValueError(f"读取 Excel 失败：缺少 pandas/openpyxl 依赖。{exc}") from exc
    try:
        frame = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"文件解析失败：{exc}") from exc
    return frame.to_dict(orient="records")


def _clean_cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def parse_creator_screening_file(filename: str, content: bytes) -> CreatorImportResult:
    if not filename:
        raise ValueError("缺少文件名")
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("文件过大，请控制在 10MB 以内")

    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        rows = _read_csv_rows(content)
    elif suffix in {".xlsx", ".xls"}:
        rows = _read_excel_rows(content)
    else:
        raise ValueError("仅支持 csv、xlsx、xls 文件")

    headers = set(rows[0].keys()) if rows else set()
    missing = [name for name in REQUIRED_CREATOR_COLUMNS if name not in headers]
    if missing:
        raise ValueError(f"缺少必需列：{'、'.join(missing)}")

    result = CreatorImportResult()
    seen: dict[str, int] = {}
    for excel_row, row in enumerate(rows, start=2):
        nickname = _clean_cell(row.get("达人昵称"))
        blogger_id = _clean_cell(row.get("博主ID"))
        profile_url = _clean_cell(row.get("主页链接"))
        price = _clean_cell(row.get("达人价格"))
        if not profile_url:
            result.invalid_rows.append({"row": excel_row, "reason": "主页链接必填"})
            continue
        identity = profile_url.lower()
        if identity in seen:
            result.invalid_rows.append({"row": excel_row, "reason": f"与第{seen[identity]}行重复"})
            continue
        seen[identity] = excel_row
        result.candidates.append(
            CreatorCandidateInput(
                index=len(result.candidates) + 1,
                nickname=nickname,
                blogger_id=blogger_id,
                profile_url=profile_url,
                price=price,
            )
        )
    return result
