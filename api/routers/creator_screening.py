# -*- coding: utf-8 -*-
import base64
import csv
import io

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..schemas.creator_screening import CreatorScreeningImportRequest, CreatorScreeningStartRequest
from ..services.creator_screening import CreatorScreeningAI, CreatorScreeningJobManager, parse_creator_screening_file


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
