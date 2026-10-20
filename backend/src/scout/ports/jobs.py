"""Job queue port (Postgres-backed in production, in-memory in tests)."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue


class JobStatus(StrEnum):
    """Lifecycle of a job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    DEAD = "dead"


class JobSpec(BaseModel):
    """What to enqueue."""

    model_config = ConfigDict(frozen=True)

    kind: str
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    run_at: AwareDatetime | None = None
    max_attempts: int = Field(default=5, ge=1)
    dedupe_key: str | None = None


class Job(BaseModel):
    """A claimed or stored job."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    kind: str
    payload: dict[str, JsonValue]
    status: JobStatus
    attempts: int
    max_attempts: int
    run_at: AwareDatetime
    locked_by: str | None = None
    locked_at: AwareDatetime | None = None
    last_error: str | None = None


class JobQueuePort(Protocol):
    """Durable queue with at-most-one concurrent claimer per job."""

    async def enqueue(self, job: JobSpec) -> UUID:
        """Store a job; the same active ``(kind, dedupe_key)`` returns the existing id."""
        ...

    async def claim(self, worker_id: str, kinds: list[str]) -> Job | None:
        """Lock the next due job of one of ``kinds`` and bump its attempts, or return None."""
        ...

    async def complete(self, job_id: UUID, result: dict[str, JsonValue]) -> None:
        """Mark the job succeeded."""
        ...

    async def fail(self, job_id: UUID, error: str, retry_in: int | None) -> None:
        """Requeue after ``retry_in`` seconds, or mark dead when None or attempts are exhausted."""
        ...

    async def heartbeat(self, job_id: UUID, worker_id: str) -> bool:
        """Extend the lease of a running job held by ``worker_id``; False when the job is no longer ours."""
        ...


def retry_delay(attempt: int) -> int:
    """Exponential back-off in seconds: 5, 10, 20, ... capped at 600."""
    delay: int = min(5 * 2 ** max(attempt - 1, 0), 600)
    return delay
