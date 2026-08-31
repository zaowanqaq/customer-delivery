# -*- coding: utf-8 -*-
import httpx
import pytest

from api.services.creator_screening import (
    CreatorScreeningAI,
    ProfileSnapshot,
    RequirementRules,
)


@pytest.mark.asyncio
async def test_creator_screening_ai_uses_requirement_tags_for_creator_type(monkeypatch):
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")
    calls = []

    async def fake_post_json(url, headers, body):
        calls.append((url, headers, body))
        return {
            "choices": [{"message": {"content": '{"status":"符合","matched_tags":["浙江本地","线下打卡"],"reason":"可见主页显示浙江和线下活动","evidence":["IP属地：浙江","笔记卡片：线下打卡"],"uncertainties":[]}'}}]
        }

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.evaluate(
        RequirementRules(tags=["浙江本地", "线下打卡", "形象匹配"]),
        ProfileSnapshot(profile_url="https://www.xiaohongshu.com/user/profile/a", visible_text="IP属地：浙江 线下打卡"),
    )

    assert result.status == "符合"
    assert result.creator_type == "浙江本地｜线下打卡"
    assert calls[0][1]["Authorization"] == "Bearer siliconflow-test"
    assert calls[0][2]["model"] == "Pro/moonshotai/Kimi-K2.6"
    assert calls[0][2]["max_tokens"] == 600


@pytest.mark.asyncio
async def test_creator_screening_ai_shows_siliconflow_rate_limit_detail(monkeypatch):
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")
    request = httpx.Request("POST", "https://api.siliconflow.cn/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        json={"error": {"message": "Provider returned error", "metadata": {"raw": "model is temporarily rate-limited upstream"}}},
    )

    async def fake_post_json(url, headers, body):
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.evaluate(
        RequirementRules(tags=["线下打卡"]),
        ProfileSnapshot(profile_url="https://www.xiaohongshu.com/user/profile/a", visible_text="IP属地：浙江"),
    )

    assert result.status == "异常"
    assert "SiliconFlow Kimi HTTP 429" in result.reason
    assert "temporarily rate-limited" in result.reason


@pytest.mark.asyncio
async def test_creator_screening_ai_uses_fixed_kimi_multimodal_model(monkeypatch):
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")
    calls = []

    async def fake_post_json(url, headers, body):
        calls.append(body)
        return {"choices": [{"message": {"content": '{"status":"待人工确认","matched_tags":[],"reason":"","evidence":[],"uncertainties":[]}'}}]}

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    await client.evaluate(
        RequirementRules(tags=["线下打卡"]),
        ProfileSnapshot(profile_url="https://www.xiaohongshu.com/user/profile/a", visible_text="IP属地：浙江"),
    )

    assert calls[0]["model"] == "Pro/moonshotai/Kimi-K2.6"


@pytest.mark.asyncio
async def test_creator_screening_prefers_siliconflow_kimi_for_requirements_and_vision(monkeypatch):
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")
    calls = []

    async def fake_post_json(url, headers, body):
        calls.append((url, headers, body))
        if len(calls) == 1:
            return {"choices": [{"message": {"content": '{"tags":["线下打卡"]}'}}]}
        return {"choices": [{"message": {"content": '{"status":"符合","matched_tags":["线下打卡"],"reason":"可见打卡内容","evidence":[],"uncertainties":[]}'}}]}

    monkeypatch.setattr(client, "_post_json", fake_post_json)
    rules = await client.parse_requirement("需要线下打卡达人")
    result = await client.evaluate(
        rules,
        ProfileSnapshot(profile_url="https://www.xiaohongshu.com/user/profile/a", visible_text="线下打卡"),
    )

    assert result.status == "符合"
    assert all(call[0] == "https://api.siliconflow.cn/v1/chat/completions" for call in calls)
    assert all(call[1]["Authorization"] == "Bearer siliconflow-test" for call in calls)
    assert all(call[2]["model"] == "Pro/moonshotai/Kimi-K2.6" for call in calls)
    assert calls[0][2]["max_tokens"] == 200
    assert calls[1][2]["max_tokens"] == 600


@pytest.mark.asyncio
async def test_creator_screening_ai_marks_invalid_model_response_as_exception():
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")

    result = await client.to_decision("not-json", RequirementRules(tags=["线下打卡"]))

    assert result.status == "异常"
    assert result.creator_type == ""


@pytest.mark.asyncio
async def test_creator_screening_ai_marks_low_confidence_as_manual_review():
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")

    result = await client.to_decision(
        '{"status":"符合","matched_tags":["线下打卡"],"reason":"信息不完整","evidence":[],"uncertainties":["低置信度"]}',
        RequirementRules(tags=["线下打卡"]),
    )

    assert result.status == "待人工确认"
    assert result.creator_type == "线下打卡"


@pytest.mark.asyncio
async def test_creator_screening_ai_keeps_login_only_snapshot_for_manual_review():
    client = CreatorScreeningAI(siliconflow_key="siliconflow-test")

    result = await client.evaluate(
        RequirementRules(tags=["线下打卡"]),
        ProfileSnapshot(
            profile_url="https://www.xiaohongshu.com/user/profile/a",
            status="待人工确认",
            error="主页只显示登录页，未找到可见资料",
        ),
    )

    assert result.status == "待人工确认"
    assert "登录页" in result.reason
