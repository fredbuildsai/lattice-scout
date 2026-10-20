"""PostgreSQL job queue using ``SELECT ... FOR UPDATE SKIP LOCKED``. Times come from the injected clock."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from scout.adapters.persistence.db import Database
from scout.ports.jobs import Job, JobSpec
from scout.shared import Clock, IdGenerator

_CLAIM = text(
    """
    UPDATE job SET status = 'running', attempts = attempts + 1, locked_by = :worker, locked_at = :now,
                   updated_at = :now
    WHERE id = (
        SELECT id FROM job
        WHERE kind = ANY(:kinds) AND run_at <= :now
          AND (status = 'queued' OR (status = 'running' AND locked_at < :expired AND attempts < max_attempts))
        ORDER BY run_at, created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1)
    RETURNING id, kind, payload, status, attempts, max_attempts, run_at, locked_by, locked_at, last_error
    """
)


class PostgresJobQueue:
    """Implements ``JobQueuePort`` over the ``job`` table."""

    def __init__(self, db: Database, clock: Clock, ids: IdGenerator, lease_seconds: int = 300) -> None:
        self._db, self._clock, self._ids = db, clock, ids
        self._lease = timedelta(seconds=lease_seconds)

    async def enqueue(self, job: JobSpec) -> UUID:
        """Insert a job, or return the id of the active job with the same dedupe key."""
        now = self._clock.now()
        jid = self._ids.new()
        async with self._db.engine.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO job (id, kind, payload, max_attempts, run_at, dedupe_key, created_at, updated_at) "
                    "VALUES (:id, :kind, CAST(:payload AS jsonb), :max, :run_at, :dedupe, :now, :now) "
                    "ON CONFLICT (kind, dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running') "
                    "DO NOTHING RETURNING id"
                ),
                {"id": jid, "kind": job.kind, "payload": json.dumps(job.payload), "max": job.max_attempts,
                 "run_at": job.run_at or now, "dedupe": job.dedupe_key, "now": now},
            )  # fmt: skip
            if result.first() is None:
                existing = (
                    await conn.execute(
                        text(
                            "SELECT id FROM job WHERE kind = :k AND dedupe_key = :d AND status IN ('queued','running')"
                        ),
                        {"k": job.kind, "d": job.dedupe_key},
                    )
                ).first()
                if existing:
                    return UUID(str(existing[0]))
        return jid

    async def _bury_exhausted(self, conn: AsyncConnection, now: datetime) -> None:
        """Jobs whose lease expired with no attempts left are dead (a crashing job must not loop forever)."""
        await conn.execute(
            text(
                "UPDATE job SET status='dead', last_error=coalesce(last_error, 'lease expired'), locked_by=NULL, "
                "updated_at=:now WHERE status='running' AND locked_at < :expired AND attempts >= max_attempts"
            ),
            {"now": now, "expired": now - self._lease},
        )

    async def claim(self, worker_id: str, kinds: list[str]) -> Job | None:
        """Lock the next due job (or a job whose lease expired)."""
        now = self._clock.now()
        async with self._db.engine.begin() as conn:
            await self._bury_exhausted(conn, now)
            row = (
                (
                    await conn.execute(
                        _CLAIM,
                        {"worker": worker_id, "kinds": kinds, "now": now, "expired": now - self._lease},
                    )
                )
                .mappings()
                .first()
            )
        return Job.model_validate(dict(row)) if row else None

    async def complete(self, job_id: UUID, result: dict[str, JsonValue]) -> None:
        """Mark succeeded and store the result."""
        async with self._db.engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE job SET status='succeeded', result=CAST(:r AS jsonb), locked_by=NULL, "
                    "updated_at=:now WHERE id=:id AND status='running'"
                ),
                {"r": json.dumps(result), "now": self._clock.now(), "id": job_id},
            )

    async def fail(self, job_id: UUID, error: str, retry_in: int | None) -> None:
        """Requeue in ``retry_in`` seconds, or mark dead when out of attempts / no retry requested."""
        now = self._clock.now()
        run_at = now + timedelta(seconds=retry_in or 0)
        async with self._db.engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE job SET
                      status = CASE WHEN :retry AND attempts < max_attempts THEN 'queued' ELSE 'dead' END,
                      run_at = CASE WHEN :retry AND attempts < max_attempts THEN :run_at ELSE run_at END,
                      last_error = :err, locked_by = NULL, locked_at = NULL, updated_at = :now
                    WHERE id = :id AND status = 'running'
                    """
                ),
                {"retry": retry_in is not None, "run_at": run_at, "err": error, "now": now, "id": job_id},
            )
