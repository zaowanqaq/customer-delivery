# -*- coding: utf-8 -*-
import asyncio

import pytest

from api.routers import crawler
from api.schemas import CollaborationMonitorStartRequest, CollaborationMonitorStopRequest


def _monitor_request() -> CollaborationMonitorStartRequest:
    return CollaborationMonitorStartRequest(
        base_token="base_token",
        table_id="tbl_monitor",
        interval_hours=4,
        creator_ids="creator_a",
    )


@pytest.fixture(autouse=True)
def clear_collaboration_jobs():
    crawler.collaboration_monitor_jobs.clear()
    yield
    for job in list(crawler.collaboration_monitor_jobs.values()):
        task = job.get("task")
        if task:
            task.cancel()
    crawler.collaboration_monitor_jobs.clear()


@pytest.mark.asyncio
async def test_collaboration_monitor_waits_interval_before_second_run(monkeypatch):
    calls = []

    async def fake_refresh(request):
        calls.append("refresh")

    async def fake_sync(request, monitor_tag):
        calls.append(f"sync:{monitor_tag}")
        return {"created": len(calls)}

    sleep_started = asyncio.Event()

    async def fake_sleep(seconds):
        calls.append(f"sleep:{seconds}")
        sleep_started.set()
        await asyncio.Future()

    monkeypatch.setattr(crawler, "_refresh_collab_creator_notes", fake_refresh)
    monkeypatch.setattr(crawler, "_sync_collaboration_snapshot", fake_sync)
    monkeypatch.setattr(crawler.asyncio, "sleep", fake_sleep)

    response = await crawler.start_collaboration_monitor(_monitor_request())
    await asyncio.wait_for(sleep_started.wait(), timeout=1)

    assert response["status"] == "ok"
    assert calls == ["refresh", "sync:4h", "sleep:14400"]


@pytest.mark.asyncio
async def test_stop_collaboration_monitor_handles_cancelled_task():
    async def sleeper():
        await asyncio.Future()

    task = asyncio.create_task(sleeper())
    crawler.collaboration_monitor_jobs["job-1"] = {"job_id": "job-1", "task": task}

    response = await crawler.stop_collaboration_monitor(CollaborationMonitorStopRequest(job_id="job-1"))

    assert response == {"status": "ok", "message": "合作笔记监控已停止", "job_id": "job-1"}
    assert "job-1" not in crawler.collaboration_monitor_jobs
