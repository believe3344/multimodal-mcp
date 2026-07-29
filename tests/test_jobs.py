import asyncio

import pytest

from jobs import JobManager, JobStatus


@pytest.mark.asyncio
async def test_submit_deduplicates_active_job_and_serializes_result() -> None:
    manager = JobManager(result_ttl=60, max_entries=8, total_timeout=5)
    calls = 0

    async def runner(job):
        nonlocal calls
        calls += 1
        job.set_total_units(1)
        job.complete_unit("image", "recognized")
        return "recognized"

    first = manager.submit(kind="image", dedupe_key="same", runner=runner)
    second = manager.submit(kind="image", dedupe_key="same", runner=runner)
    assert first.job_id == second.job_id
    await manager.wait(first.job_id, timeout=1)
    snapshot = manager.snapshot(first.job_id)
    assert snapshot["status"] == JobStatus.COMPLETED.value
    assert snapshot["result"] == "recognized"
    assert snapshot["completed_units"] == 1
    assert calls == 1


@pytest.mark.asyncio
async def test_wait_timeout_does_not_cancel_background_task() -> None:
    manager = JobManager(result_ttl=60, max_entries=8, total_timeout=5)
    release = asyncio.Event()

    async def runner(job):
        job.set_total_units(1)
        await release.wait()
        job.complete_unit("image", "done")
        return "done"

    job = manager.submit(kind="image", dedupe_key="slow", runner=runner)
    assert await manager.wait(job.job_id, timeout=0.01) is False
    assert manager.snapshot(job.job_id)["status"] == JobStatus.PROCESSING.value
    release.set()
    assert await manager.wait(job.job_id, timeout=1) is True
    assert manager.snapshot(job.job_id)["result"] == "done"


@pytest.mark.asyncio
async def test_cancel_marks_running_job_cancelled() -> None:
    manager = JobManager(result_ttl=60, max_entries=8, total_timeout=5)

    async def runner(job):
        await asyncio.Event().wait()
        return "unreachable"

    job = manager.submit(kind="image", dedupe_key="cancel", runner=runner)
    manager.cancel(job.job_id)
    await asyncio.sleep(0)
    assert manager.snapshot(job.job_id)["status"] == JobStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_expired_terminal_job_is_removed() -> None:
    now = [10.0]
    manager = JobManager(
        result_ttl=5,
        max_entries=8,
        total_timeout=5,
        clock=lambda: now[0],
    )

    async def runner(job):
        return "done"

    job = manager.submit(kind="image", dedupe_key="ttl", runner=runner)
    await manager.wait(job.job_id, timeout=1)
    now[0] = 16.0
    with pytest.raises(KeyError):
        manager.get(job.job_id)
