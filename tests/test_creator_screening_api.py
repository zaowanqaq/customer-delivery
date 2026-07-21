# -*- coding: utf-8 -*-
import asyncio
import base64

import pytest

from api.routers import creator_screening
from api.schemas.creator_screening import (
    CreatorCandidateInput,
    CreatorScreeningApiKeyRequest,
    CreatorScreeningImportRequest,
    CreatorScreeningStartRequest,
    CreatorScreeningSyncRequest,
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


async def fake_snapshot(candidate, job_id, worker_index=0):
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
async def test_finished_job_syncs_only_approved_creators(monkeypatch):
    manager = CreatorScreeningJobManager(ai=FakeAI(), snapshot_collector=fake_snapshot)
    monkeypatch.setattr(creator_screening, "screening_manager", manager)
    candidates = [
        CreatorCandidateInput(
            index=1,
            nickname="甲",
            blogger_id="a",
            profile_url="https://www.xiaohongshu.com/user/profile/a",
            price="100",
        )
    ]
    job = await manager.start("浙江线下打卡", candidates)
    await job.task

    async def fake_read_defs(*_args):
        return creator_screening._creator_screening_result_fields()

    async def fake_existing(*_args):
        return {}

    async def fake_order(*_args):
        return None

    commands = []

    async def fake_run(command, **_kwargs):
        commands.append(command)
        return {"ok": True, "data": {"record_id_list": ["rec1"]}}

    monkeypatch.setattr(creator_screening, "_read_table_field_defs", fake_read_defs)
    monkeypatch.setattr(creator_screening, "_read_existing_base_records", fake_existing)
    monkeypatch.setattr(creator_screening, "_set_table_view_field_order", fake_order)
    monkeypatch.setattr(creator_screening, "_run_lark_cli", fake_run)

    result = await creator_screening.sync_job(
        job.id,
        CreatorScreeningSyncRequest(base_token="bas1", table_id="tbl1"),
    )

    assert result["approved"] == 1
    assert result["created"] == 1
    assert result["updated"] == 0
    assert any("+record-batch-create" in command for command in commands)


@pytest.mark.asyncio
async def test_job_runs_profiles_with_bounded_parallel_workers_and_keeps_input_order(monkeypatch):
    active_workers = 0
    peak_workers = 0

    async def parallel_snapshot(candidate, job_id, worker_index=0):
        nonlocal active_workers, peak_workers
        active_workers += 1
        peak_workers = max(peak_workers, active_workers)
        await asyncio.sleep(0.01)
        active_workers -= 1
        return ProfileSnapshot(profile_url=candidate.profile_url, visible_text="IP属地：浙江", ip_location="浙江")

    monkeypatch.setenv("CREATOR_SCREENING_CONCURRENCY", "2")
    manager = CreatorScreeningJobManager(ai=FakeAI(), snapshot_collector=parallel_snapshot)
    candidates = [
        CreatorCandidateInput(index=index, nickname=f"达人{index}", blogger_id=str(index), profile_url=f"https://xhs.test/{index}")
        for index in (1, 2, 3)
    ]

    job = await manager.start("浙江线下打卡", candidates)
    await job.task

    assert peak_workers == 2
    assert [row["序号"] for row in job.to_payload()["results"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_preflight_reports_missing_model_keys_without_leaking_values(monkeypatch, tmp_path):
    monkeypatch.setattr("api.services.local_ai_config.local_ai_config_path", lambda: tmp_path / "missing-local-ai-config.json")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "ignored-environment-key")

    result = await creator_screening.preflight()

    assert result == {
        "siliconflow_configured": False,
        "active_provider": "SiliconFlow Kimi",
        "active_model": "Pro/moonshotai/Kimi-K2.6",
        "configuration_source": "",
    }


@pytest.mark.asyncio
async def test_save_api_key_persists_locally_without_returning_key(monkeypatch, tmp_path):
    config_path = tmp_path / "creator_screening_ai.json"
    monkeypatch.setattr("api.services.local_ai_config.local_ai_config_path", lambda: config_path)

    result = await creator_screening.save_api_key(CreatorScreeningApiKeyRequest(api_key="secret-key"))

    assert result == {
        "status": "ok",
        "active_provider": "SiliconFlow Kimi",
        "active_model": "Pro/moonshotai/Kimi-K2.6",
        "configuration_source": "本机网页配置",
    }
    assert "secret-key" not in result.values()
    assert config_path.exists()
    assert "secret-key" in config_path.read_text(encoding="utf-8")


def test_web_saved_api_key_is_the_only_runtime_source(monkeypatch, tmp_path):
    config_path = tmp_path / "creator_screening_ai.json"
    monkeypatch.setattr("api.services.local_ai_config.local_ai_config_path", lambda: config_path)
    config_path.write_text('{"siliconflow_api_key": "web-key"}', encoding="utf-8")

    ai = creator_screening.CreatorScreeningAI()

    assert ai._siliconflow_key == "web-key"
    assert ai.configuration_status()["configuration_source"] == "本机网页配置"
