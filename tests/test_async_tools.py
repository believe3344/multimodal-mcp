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
async def test_compat_tool_waits_for_slow_task_and_returns_late_result(monkeypatch) -> None:
    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_run(job, request):
        job.set_total_units(1)
        started.set()
        await release.wait()
        job.complete_unit("image", "late result")
        return "late result"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    call = asyncio.create_task(server.describe_image(image="image"))
    try:
        await started.wait()
        await asyncio.sleep(0.02)
        assert call.done() is False
        release.set()
        assert await call == "late result"
    finally:
        release.set()
        await asyncio.gather(call, return_exceptions=True)


@pytest.mark.asyncio
async def test_compat_tool_returns_terminal_failure(monkeypatch) -> None:
    async def fake_run(job, request):
        raise RuntimeError("vision unavailable")

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )

    assert await server.describe_image(image="image") == (
        "[describe_image failed] RuntimeError: RuntimeError: vision unavailable"
    )


@pytest.mark.asyncio
async def test_compat_tool_returns_terminal_cancelled(monkeypatch) -> None:
    async def fake_run(job, request):
        await asyncio.Event().wait()
        return "unreachable"

    manager = JobManager(result_ttl=60, max_entries=8, total_timeout=5)
    submitted = asyncio.Event()
    captured = []
    original_submit = server._submit_request

    def capture_submit(request):
        job = original_submit(request)
        captured.append(job)
        submitted.set()
        return job

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(server, "JOBS", manager)
    monkeypatch.setattr(server, "_submit_request", capture_submit)
    call = asyncio.create_task(server.describe_image(image="image"))
    await submitted.wait()
    manager.cancel(captured[0].job_id)

    assert await call == "[describe_image failed] RuntimeError: cancelled"


@pytest.mark.asyncio
async def test_compat_tool_returns_total_timeout_failure(monkeypatch) -> None:
    async def fake_run(job, request):
        await asyncio.Event().wait()
        return "unreachable"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=0.01),
    )

    assert await server.describe_image(image="image") == (
        "[describe_image failed] RuntimeError: job exceeded 0s total timeout"
    )


@pytest.mark.asyncio
async def test_compat_tool_deduplicates_concurrent_requests(monkeypatch) -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def fake_run(job, request):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "done"

    monkeypatch.setattr(server.RUNNER, "run", fake_run)
    monkeypatch.setattr(
        server,
        "JOBS",
        JobManager(result_ttl=60, max_entries=8, total_timeout=5),
    )
    first = asyncio.create_task(server.describe_image(image="same"))
    second = asyncio.create_task(server.describe_image(image="same"))
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert calls == 1
        assert first.done() is False
        assert second.done() is False
        release.set()
        assert await asyncio.gather(first, second) == ["done", "done"]
        assert calls == 1
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_get_recognition_accepts_single_long_wait(monkeypatch) -> None:
    async def fake_run(job, request):
        job.set_total_units(1)
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
        )
    )

    completed = json.loads(
        await server.get_recognition(started["job_id"], wait_seconds=50)
    )
    assert completed["status"] == "completed"


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
