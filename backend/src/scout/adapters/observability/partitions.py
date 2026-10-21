"""Monthly partition maintenance for the observability tables (D13)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta

from pydantic import JsonValue
from sqlalchemy import text

from scout.adapters.persistence.db import Database
from scout.ports.jobs import Job, JobQueuePort, JobSpec
from scout.shared import Clock

TABLES = ("analytics_event", "llm_call", "external_call")
MONTHS_AHEAD = 3
KIND = "maintenance.ensure_partitions"


def _add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    return date(idx // 12, idx % 12 + 1, 1)


def next_run(now: datetime) -> datetime:
    """Tomorrow at 00:05 UTC."""
    return datetime.combine((now + timedelta(days=1)).date(), time(0, 5), tzinfo=UTC)


async def ensure_partitions(db: Database, clock: Clock) -> int:
    """Create (idempotently) partitions for this month and the next ``MONTHS_AHEAD``; returns the calls made."""
    first = clock.now().date().replace(day=1)
    n = 0
    async with db.engine.begin() as conn:
        for table in TABLES:
            for offset in range(MONTHS_AHEAD + 1):
                await conn.execute(
                    text("SELECT public.scout_ensure_month_partition(CAST(:t AS regclass), :m)"),
                    {"t": table, "m": _add_months(first, offset)},
                )
                n += 1
    return n


async def schedule_today(queue: JobQueuePort, clock: Clock) -> None:
    """Enqueue today's run (deduplicated by date, so worker restarts do not pile up jobs)."""
    now = clock.now()
    await queue.enqueue(JobSpec(kind=KIND, dedupe_key=now.date().isoformat(), run_at=now))


def make_handler(
    db: Database, clock: Clock, queue: JobQueuePort
) -> Callable[[Job], Awaitable[dict[str, JsonValue]]]:
    """Job handler: ensure partitions, then re-enqueue itself for tomorrow."""

    async def handler(job: Job) -> dict[str, JsonValue]:
        n = await ensure_partitions(db, clock)
        when = next_run(clock.now())
        await queue.enqueue(JobSpec(kind=KIND, dedupe_key=when.date().isoformat(), run_at=when))
        return {"ensured": n}

    return handler
