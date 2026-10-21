"""Monthly partition maintenance for the observability tables (D13)."""

from __future__ import annotations

import re
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


_PART = re.compile(r"^(?P<base>.+)_y(?P<y>\d{4})m(?P<m>\d{2})$")


async def drop_expired_partitions(db: Database, clock: Clock, keep_months: int) -> int:
    """Drop monthly partitions that end before the retention window; returns how many were dropped.

    Raw usage and analytics data is kept ``keep_months`` full months plus the current one (13 months by default).
    """
    first = clock.now().date().replace(day=1)
    cutoff = _add_months(first, -keep_months)
    dropped = 0
    async with db.engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = ANY(:t)"
            ),
            {"t": list(TABLES)},
        )
        for (name,) in rows.all():
            m = _PART.match(name)
            if m and m["base"] in TABLES and date(int(m["y"]), int(m["m"]), 1) < cutoff:
                await conn.execute(text(f'DROP TABLE "{name}"'))
                dropped += 1
    return dropped


def make_handler(
    db: Database, clock: Clock, queue: JobQueuePort, keep_months: int = 13
) -> Callable[[Job], Awaitable[dict[str, JsonValue]]]:
    """Job handler: ensure partitions, drop expired ones, then re-enqueue itself for tomorrow."""

    async def handler(job: Job) -> dict[str, JsonValue]:
        n = await ensure_partitions(db, clock)
        dropped = await drop_expired_partitions(db, clock, keep_months)
        when = next_run(clock.now())
        await queue.enqueue(JobSpec(kind=KIND, dedupe_key=when.date().isoformat(), run_at=when))
        return {"ensured": n, "dropped": dropped}

    return handler
