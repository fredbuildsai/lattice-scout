"""In-memory JobQueuePort."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import JsonValue

from scout.ports.jobs import Job, JobSpec, JobStatus
from scout.shared import Clock, IdGenerator


class InMemoryJobQueue:
    """Same semantics as the Postgres queue (see tests/contract/test_job_queue.py)."""

    def __init__(self, clock: Clock, ids: IdGenerator, lease_seconds: int = 300) -> None:
        self.clock, self.ids, self.lease = clock, ids, timedelta(seconds=lease_seconds)
        self.jobs: dict[UUID, Job] = {}
        self.dedupe: dict[UUID, tuple[str, str]] = {}
        self.results: dict[UUID, dict[str, JsonValue]] = {}

    async def enqueue(self, job: JobSpec) -> UUID:
        if job.dedupe_key is not None:
            for jid, key in self.dedupe.items():
                if key == (job.kind, job.dedupe_key) and self.jobs[jid].status in (
                    JobStatus.QUEUED,
                    JobStatus.RUNNING,
                ):
                    return jid
        jid = self.ids.new()
        self.jobs[jid] = Job(
            id=jid, kind=job.kind, payload=job.payload, status=JobStatus.QUEUED, attempts=0,
            max_attempts=job.max_attempts, run_at=job.run_at or self.clock.now(),
        )  # fmt: skip
        if job.dedupe_key is not None:
            self.dedupe[jid] = (job.kind, job.dedupe_key)
        return jid

    async def claim(self, worker_id: str, kinds: list[str]) -> Job | None:
        now = self.clock.now()
        due = [
            j for j in self.jobs.values()
            if j.kind in kinds and j.run_at <= now and (
                j.status == JobStatus.QUEUED
                or (
                    j.status == JobStatus.RUNNING
                    and j.locked_at
                    and now - j.locked_at > self.lease
                    and j.attempts < j.max_attempts
                )
            )
        ]  # fmt: skip
        if not due:
            return None
        job = min(due, key=lambda j: (j.run_at, str(j.id)))
        claimed = job.model_copy(
            update={
                "status": JobStatus.RUNNING,
                "attempts": job.attempts + 1,
                "locked_by": worker_id,
                "locked_at": now,
            }
        )
        self.jobs[job.id] = claimed
        return claimed

    async def complete(self, job_id: UUID, result: dict[str, JsonValue]) -> None:
        self.jobs[job_id] = self.jobs[job_id].model_copy(
            update={"status": JobStatus.SUCCEEDED, "locked_by": None}
        )
        self.results[job_id] = result

    async def fail(self, job_id: UUID, error: str, retry_in: int | None) -> None:
        job = self.jobs[job_id]
        if retry_in is None or job.attempts >= job.max_attempts:
            upd: dict[str, Any] = {"status": JobStatus.DEAD, "last_error": error, "locked_by": None}
        else:
            upd = {
                "status": JobStatus.QUEUED, "last_error": error, "locked_by": None, "locked_at": None,
                "run_at": self.clock.now() + timedelta(seconds=retry_in),
            }  # fmt: skip
        self.jobs[job_id] = job.model_copy(update=upd)
