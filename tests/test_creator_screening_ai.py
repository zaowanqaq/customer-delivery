# -*- coding: utf-8 -*-
import pytest

from api.services.creator_screening import (
    CreatorScreeningAI,
    ProfileSnapshot,
    RequirementRules,
)


@pytest.mark.asyncio
async def test_creator_screening_ai_uses_requirement_tags_for_creator_type(monkeypatch):
    client = CreatorScreeningAI(deepseek_key="deepseek-test", openrouter_key="openrouter-test")
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
    assert calls[0][1]["Authorization"] == "Bearer openrouter-test"
    assert calls[0][2]["model"] == "google/gemma-4-31b-it:free"


@pytest.mark.asyncio
async def test_creator_screening_ai_marks_invalid_model_response_as_exception():
    client = CreatorScreeningAI(deepseek_key="d", openrouter_key="o")

    result = await client.to_decision("not-json", RequirementRules(tags=["线下打卡"]))

    assert result.status == "异常"
    assert result.creator_type == ""


@pytest.mark.asyncio
async def test_creator_screening_ai_marks_low_confidence_as_manual_review():
    client = CreatorScreeningAI(deepseek_key="d", openrouter_key="o")

    result = await client.to_decision(
        '{"status":"符合","matched_tags":["线下打卡"],"reason":"信息不完整","evidence":[],"uncertainties":["低置信度"]}',
        RequirementRules(tags=["线下打卡"]),
    )

    assert result.status == "待人工确认"
    assert result.creator_type == "线下打卡"
