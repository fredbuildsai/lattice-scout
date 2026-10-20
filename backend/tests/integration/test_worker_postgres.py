import pytest

from scout.adapters.persistence.db import Database
from scout.adapters.queue.postgres import PostgresJobQueue
from scout.ports.jobs import JobSpec, JobStatus
from scout.shared import SystemClock, UuidGenerator
from scout.worker.loop import DEFAULT_HANDLERS, run_once

pytestmark = pytest.mark.integration


async def test_enqueued_ping_completes(db: Database) -> None:
    q = PostgresJobQueue(db, SystemClock(), UuidGenerator())
    await q.enqueue(JobSpec(kind="ping"))
    assert await run_once(q, DEFAULT_HANDLERS, "w1")
    from sqlalchemy import text

    async with db.engine.connect() as conn:
        row = (await conn.execute(text("SELECT status, result FROM job"))).one()
    assert row[0] == JobStatus.SUCCEEDED and row[1] == {"pong": True}


async def test_partition_job_runs_and_reschedules(db: Database) -> None:
    from scout.adapters.observability.partitions import KIND, schedule_today
    from scout.worker.loop import build_handlers

    clock, ids = SystemClock(), UuidGenerator()
    q = PostgresJobQueue(db, clock, ids)
    await schedule_today(q, clock)
    await schedule_today(q, clock)  # deduplicated
    handlers = build_handlers(db, clock, q)
    assert await run_once(q, handlers, "w")
    assert not await run_once(q, handlers, "w")  # tomorrow's job is not due yet
    from sqlalchemy import text

    async with db.engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT status FROM job WHERE kind = :k ORDER BY run_at"), {"k": KIND})
        ).all()
    assert [r[0] for r in rows] == ["succeeded", "queued"]
