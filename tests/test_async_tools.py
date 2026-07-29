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
