import asyncio
import json

import pytest

import server
from jobs import JobManager


@pytest.mark.asyncio
async def test_start_and_get_recognition(monkeypatch) -> None:
    release = asyncio.Event()

    async def fake_run(job, request):
        job.set_total_units(1)
        await release.wait()
        job.complete_unit("image", "done")
        return "done"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    started = json.loads(
        await server.start_recognition(
            kind=server.RecognitionKind.IMAGE,
            sources=["image"],
            instruction="read",
            detail=server.DetailLevel.HIGH,
        )
    )
    assert started["status"] in {"queued", "processing"}
    processing = json.loads(await server.get_recognition(started["job_id"]))
    assert processing["status"] in {"queued", "processing"}
    release.set()
    completed = json.loads(
        await server.get_recognition(started["job_id"], wait_seconds=1)
    )
    assert completed["status"] == "completed"
    assert completed["result"] == "done"


@pytest.mark.asyncio
async def test_cancel_recognition(monkeypatch) -> None:
    async def fake_run(job, request):
        await asyncio.Event().wait()
        return "unreachable"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    started = json.loads(
        await server.start_recognition(
            kind=server.RecognitionKind.IMAGE,
            sources=["image"],
        )
    )
    await server.cancel_recognition(started["job_id"])
    await asyncio.sleep(0)
    status = json.loads(await server.get_recognition(started["job_id"]))
    assert status["status"] == "cancelled"


@pytest.mark.asyncio
async def test_compat_tool_returns_result_when_job_finishes_quickly(monkeypatch) -> None:
    async def fake_run(job, request):
        job.set_total_units(1)
        job.complete_unit("image", "quick result")
        return "quick result"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    assert await server.describe_image(image="image") == "quick result"


@pytest.mark.asyncio
async def test_compat_tool_returns_job_without_cancelling_slow_task(monkeypatch) -> None:
    release = asyncio.Event()

    async def fake_run(job, request):
        job.set_total_units(1)
        await release.wait()
        job.complete_unit("image", "late result")
        return "late result"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(server, "SYNC_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    response = json.loads(await server.describe_image(image="image"))
    assert response["status"] == "processing"
    release.set()
    completed = json.loads(
        await server.get_recognition(response["job_id"], wait_seconds=1)
    )
    assert completed["result"] == "late result"


@pytest.mark.asyncio
async def test_cache_status_reports_jobs_and_clear_all_cancels_them(monkeypatch) -> None:
    async def fake_run(job, request):
        await asyncio.Event().wait()
        return "unreachable"

    manager = JobManager(result_ttl=60, max_entries=8, total_timeout=5)
    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(server, "JOBS", manager)
    await server.start_recognition(kind=server.RecognitionKind.IMAGE, sources=["x"])
    status = json.loads(await server.multimodal_cache_status())
    assert status["jobs"]["active"] == 1
    cleared = json.loads(await server.clear_multimodal_state(server.StateTarget.ALL))
    assert cleared["jobs_cancelled"] == 1
