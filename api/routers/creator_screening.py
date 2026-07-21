# -*- coding: utf-8 -*-
import base64
import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas.creator_screening import (
    CreatorScreeningApiKeyRequest,
    CreatorScreeningImportRequest,
    CreatorScreeningStartRequest,
    CreatorScreeningSyncRequest,
)
from ..services.local_ai_config import save_siliconflow_api_key
from ..services.creator_screening import CreatorScreeningAI, CreatorScreeningJobManager, parse_creator_screening_file
from .crawler import (
    _base_record_field_map,
    _chunk_table_rows,
    _create_base_field,
    _creator_screening_result_fields,
    _find_lark_cli,
    _lark_json_arg,
    _read_existing_base_records,
    _read_table_field_defs,
    _run_lark_cli,
    _set_table_view_field_order,
)


router = APIRouter(prefix="/creator-screening", tags=["creator-screening"])
screening_manager = CreatorScreeningJobManager()


@router.get("/template")
async def download_template():
    output = io.StringIO()
    csv.writer(output).writerow(["达人昵称", "博主ID", "主页链接", "达人价格"])
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=creator_screening_template.csv"},
    )


@router.get("/preflight")
async def preflight():
    return CreatorScreeningAI().configuration_status()


@router.post("/api-key")
async def save_api_key(request: CreatorScreeningApiKeyRequest):
    api_key = request.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="请粘贴 API Key")
    save_siliconflow_api_key(api_key)
    return {
        "status": "ok",
        "active_provider": "SiliconFlow Kimi",
        "active_model": "Pro/moonshotai/Kimi-K2.6",
        "configuration_source": "本机网页配置",
    }


@router.post("/import")
async def import_candidates(request: CreatorScreeningImportRequest):
    try:
        content = base64.b64decode(request.content_base64, validate=True)
        result = parse_creator_screening_file(request.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="文件内容不是有效 base64") from exc
    return {
        "status": "ok",
        "count": len(result.candidates),
        "candidates": [candidate.model_dump() for candidate in result.candidates],
        "invalid_rows": result.invalid_rows,
    }


@router.post("/jobs")
async def start_job(request: CreatorScreeningStartRequest):
    if not request.requirement.strip():
        raise HTTPException(status_code=400, detail="请填写筛选需求")
    if not request.candidates:
        raise HTTPException(status_code=400, detail="请先导入至少一位达人")
    if any(not item.profile_url.strip() for item in request.candidates):
        raise HTTPException(status_code=400, detail="主页链接为必填项")
    job = await screening_manager.start(request.requirement, request.candidates)
    return {"job_id": job.id, "status": "running"}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = screening_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或服务已重启")
    return job.to_payload()


@router.post("/jobs/{job_id}/sync")
async def sync_job(job_id: str, request: CreatorScreeningSyncRequest):
    """Sync only AI-approved creators to the four-column customer input layout."""
    job = screening_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或服务已重启")
    if not job.finished:
        raise HTTPException(status_code=400, detail="初筛尚未完成，请完成后再同步")
    if not request.base_token.strip() or not request.table_id.strip():
        raise HTTPException(status_code=400, detail="请先绑定 AI初筛结果表")

    desired_fields = _creator_screening_result_fields()
    desired_names = [field["name"] for field in desired_fields]
    existing_defs = await _read_table_field_defs(request.base_token, request.table_id)
    existing_names = {str(field.get("name")) for field in existing_defs if field.get("name")}
    for field in desired_fields:
        if field["name"] not in existing_names:
            await _create_base_field(request.base_token, request.table_id, field)
    await _set_table_view_field_order(request.base_token, request.table_id, desired_names)

    approved = [item for item in job.results if item.status == "符合"]
    existing = await _read_existing_base_records(request.base_token, request.table_id, "主页链接")
    created = 0
    updated = 0
    rows_to_create = []
    for item in approved:
        values = [
            item.candidate.nickname,
            item.candidate.blogger_id,
            item.profile_url or item.candidate.profile_url,
            item.candidate.price,
        ]
        record_id = existing.get(str(values[2]).strip())
        if record_id:
            payload = _base_record_field_map(desired_names, values)
            with _lark_json_arg(payload) as json_arg:
                await _run_lark_cli(
                    [
                        _find_lark_cli(), "base", "+record-upsert", "--as", "user",
                        "--base-token", request.base_token, "--table-id", request.table_id,
                        "--record-id", record_id, "--json", json_arg,
                    ],
                    timeout_sec=60,
                )
            updated += 1
        else:
            rows_to_create.append(values)

    for batch in _chunk_table_rows(rows_to_create):
        payload = {"fields": desired_names, "rows": batch}
        with _lark_json_arg(payload) as json_arg:
            await _run_lark_cli(
                [
                    _find_lark_cli(), "base", "+record-batch-create", "--as", "user",
                    "--base-token", request.base_token, "--table-id", request.table_id,
                    "--json", json_arg,
                ],
                timeout_sec=60,
            )
        created += len(batch)

    return {
        "status": "ok",
        "approved": len(approved),
        "created": created,
        "updated": updated,
        "table_id": request.table_id,
        "target_url": f"https://my.feishu.cn/base/{request.base_token}?table={request.table_id}",
    }
