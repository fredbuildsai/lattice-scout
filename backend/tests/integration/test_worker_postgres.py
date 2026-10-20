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
