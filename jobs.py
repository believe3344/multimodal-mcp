from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class JobUnit:
    unit_id: str
    status: str = "queued"
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RecognitionJob:
    job_id: str
    kind: str
    dedupe_key: str
    created_at: float
    updated_at: float
    status: JobStatus = JobStatus.QUEUED
    total_units: int = 0
    result: Optional[str] = None
    error: Optional[str] = None
    units: OrderedDict[str, JobUnit] = field(default_factory=OrderedDict)
    task: Optional[asyncio.Task[None]] = field(default=None, repr=False)

    def set_total_units(self, total: int) -> None:
        self.total_units = total
        self.updated_at = time.time()

    def complete_unit(self, unit_id: str, result: str) -> None:
        self.units[unit_id] = JobUnit(unit_id, "completed", result=result)
        self.updated_at = time.time()

    def fail_unit(self, unit_id: str, error: str) -> None:
        self.units[unit_id] = JobUnit(unit_id, "failed", error=error)
        self.updated_at = time.time()

    @property
    def completed_units(self) -> int:
        return sum(unit.status == "completed" for unit in self.units.values())

    @property
    def failed_units(self) -> int:
        return sum(unit.status == "failed" for unit in self.units.values())


Runner = Callable[[RecognitionJob], Awaitable[str]]


class JobManager:
    def __init__(
        self,
        *,
        result_ttl: float,
        max_entries: int,
        total_timeout: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.result_ttl = result_ttl
        self.max_entries = max_entries
        self.total_timeout = total_timeout
        self.clock = clock
        self._jobs: OrderedDict[str, RecognitionJob] = OrderedDict()
        self._dedupe: dict[str, str] = {}

    def _purge(self) -> None:
        now = self.clock()
        terminal = {
            JobStatus.COMPLETED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        for job_id, job in list(self._jobs.items()):
            if job.status in terminal and now - job.updated_at >= self.result_ttl:
                self._jobs.pop(job_id)
                self._dedupe.pop(job.dedupe_key, None)
        while len(self._jobs) >= self.max_entries:
            removable = next(
                (key for key, value in self._jobs.items() if value.status in terminal),
                None,
            )
            if removable is None:
                raise RuntimeError("job capacity reached; wait for an active job to finish")
            removed = self._jobs.pop(removable)
            self._dedupe.pop(removed.dedupe_key, None)

    async def _run(self, job: RecognitionJob, runner: Runner) -> None:
        job.status = JobStatus.PROCESSING
        job.updated_at = self.clock()
        try:
            job.result = await asyncio.wait_for(runner(job), self.total_timeout)
            job.status = JobStatus.PARTIAL if job.failed_units else JobStatus.COMPLETED
        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            job.error = "cancelled"
        except asyncio.TimeoutError:
            job.status = JobStatus.FAILED
            job.error = f"job exceeded {self.total_timeout:.0f}s total timeout"
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.updated_at = self.clock()

    def submit(self, *, kind: str, dedupe_key: str, runner: Runner) -> RecognitionJob:
        self._purge()
        existing_id = self._dedupe.get(dedupe_key)
        if existing_id is not None and existing_id in self._jobs:
            existing = self._jobs[existing_id]
            if existing.status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
                self._jobs.move_to_end(existing_id)
                return existing
            self._dedupe.pop(dedupe_key, None)
        now = self.clock()
        job = RecognitionJob(
            job_id=f"job_{uuid.uuid4().hex[:16]}",
            kind=kind,
            dedupe_key=dedupe_key,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.job_id] = job
        self._dedupe[dedupe_key] = job.job_id
        job.task = asyncio.create_task(self._run(job, runner), name=job.job_id)
        return job

    async def wait(self, job_id: str, timeout: float) -> bool:
        job = self.get(job_id)
        if job.task is None or job.task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(job.task), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def wait_until_done(self, job_id: str) -> None:
        job = self.get(job_id)
        if job.task is None or job.task.done():
            return
        await asyncio.shield(job.task)

    def get(self, job_id: str) -> RecognitionJob:
        self._purge()
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        self._jobs.move_to_end(job_id)
        return job

    def cancel(self, job_id: str) -> RecognitionJob:
        job = self.get(job_id)
        if job.task is not None and not job.task.done():
            job.status = JobStatus.CANCELLED
            job.error = "cancelled"
            job.updated_at = self.clock()
            job.task.cancel()
        return job

    def snapshot(self, job_id: str, *, include_partial: bool = True) -> dict[str, Any]:
        job = self.get(job_id)
        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "kind": job.kind,
            "status": job.status.value,
            "total_units": job.total_units,
            "completed_units": job.completed_units,
            "failed_units": job.failed_units,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }
        if job.result is not None and job.status in {JobStatus.COMPLETED, JobStatus.PARTIAL}:
            payload["result"] = job.result
        if job.error is not None:
            payload["error"] = job.error
        if include_partial:
            payload["units"] = [unit.__dict__.copy() for unit in job.units.values()]
        return payload

    def stats(self) -> dict[str, int]:
        self._purge()
        active = sum(
            job.status in {JobStatus.QUEUED, JobStatus.PROCESSING}
            for job in self._jobs.values()
        )
        return {
            "entries": len(self._jobs),
            "active": active,
            "terminal": len(self._jobs) - active,
        }

    def clear(self) -> int:
        cancelled = 0
        for job in self._jobs.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
                cancelled += 1
        self._jobs.clear()
        self._dedupe.clear()
        return cancelled
