# -*- coding: utf-8 -*-
import asyncio
import base64

import pytest

from api.routers import creator_screening
from api.schemas.creator_screening import (
    CreatorCandidateInput,
    CreatorScreeningImportRequest,
    CreatorScreeningStartRequest,
)
from api.services.creator_screening import (
    CreatorScreeningJobManager,
    ProfileSnapshot,
    RequirementRules,
    ScreeningDecision,
)


class FakeAI:
    async def parse_requirement(self, requirement):
        return RequirementRules(tags=["浙江本地", "线下打卡"], raw_requirement=requirement)

    async def evaluate(self, rules, snapshot):
        return ScreeningDecision(
            status="符合",
            creator_type="浙江本地｜线下打卡",
            reason="主页证据充分",
            evidence=["IP属地：浙江"],
        )


async def fake_snapshot(candidate, job_id):
    return ProfileSnapshot(
        profile_url=candidate.profile_url or "https://www.xiaohongshu.com/user/profile/" + candidate.blogger_id,
        visible_text="IP属地：浙江 线下打卡",
        ip_location="浙江",
    )


@pytest.mark.asyncio
async def test_import_endpoint_returns_candidates_and_invalid_rows():
    content = base64.b64encode(
        "达人昵称,博主ID,主页链接,达人价格\n甲,a,https://www.xiaohongshu.com/user/profile/a,100\n乙,,,200\n".encode("utf-8")
    ).decode("ascii")

    result = await creator_screening.import_candidates(
        CreatorScreeningImportRequest(filename="creators.csv", content_base64=content)
    )

    assert result["count"] == 1
    assert result["candidates"][0]["nickname"] == "甲"
    assert result["invalid_rows"] == [{"row": 3, "reason": "主页链接必填"}]


@pytest.mark.asyncio
async def test_start_job_and_status_expose_frontend_result_rows(monkeypatch):
    manager = CreatorScreeningJobManager(ai=FakeAI(), snapshot_collector=fake_snapshot)
    monkeypatch.setattr(creator_screening, "screening_manager", manager)
    request = CreatorScreeningStartRequest(
        requirement="浙江线下打卡",
        candidates=[CreatorCandidateInput(index=1, nickname="甲", blogger_id="a", profile_url="https://www.xiaohongshu.com/user/profile/a", price="100")],
    )

    started = await creator_screening.start_job(request)
    for _ in range(10):
        await asyncio.sleep(0)
        status = await creator_screening.get_job(started["job_id"])
        if status["finished"]:
            break

    assert status["progress"] == {"total": 1, "completed": 1, "pending": 0, "exceptions": 0}
    assert status["results"] == [{
        "序号": 1,
        "是否符合筛选要求": "符合",
        "达人昵称": "甲",
        "博主id": "a",
        "主页链接": "https://www.xiaohongshu.com/user/profile/a",
        "ip地": "浙江",
        "达人类型": "浙江本地｜线下打卡",
        "达人价格": "100",
        "详情": {"理由": "主页证据充分", "证据": ["IP属地：浙江"], "不确定项": []},
    }]


@pytest.mark.asyncio
async def test_unknown_job_returns_not_found(monkeypatch):
    monkeypatch.setattr(creator_screening, "screening_manager", CreatorScreeningJobManager(ai=FakeAI(), snapshot_collector=fake_snapshot))

    with pytest.raises(creator_screening.HTTPException, match="任务不存在"):
        await creator_screening.get_job("missing")


@pytest.mark.asyncio
async def test_preflight_reports_missing_model_keys_without_leaking_values(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)

    result = await creator_screening.preflight()

    assert result == {
        "deepseek_configured": False,
        "openrouter_configured": False,
        "siliconflow_configured": False,
        "active_provider": "DeepSeek + OpenRouter",
        "active_model": "google/gemma-4-31b-it:free",
    }
