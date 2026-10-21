from datetime import UTC, datetime, timedelta

from pydantic import JsonValue
from tests.fakes import InMemoryJobQueue

from scout.ports.jobs import Job, JobSpec, JobStatus
from scout.shared import FixedClock, SequentialIdGenerator
from scout.worker.loop import DEFAULT_HANDLERS, run_once

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def test_ping_job_completes() -> None:
    q = InMemoryJobQueue(FixedClock(T0), SequentialIdGenerator())
    jid = await q.enqueue(JobSpec(kind="ping"))
    assert await run_once(q, DEFAULT_HANDLERS, "w") is True
    assert q.jobs[jid].status == JobStatus.SUCCEEDED and q.results[jid] == {"pong": True}
    assert await run_once(q, DEFAULT_HANDLERS, "w") is False


async def test_failing_job_is_retried_with_backoff_then_dead() -> None:
    clock = FixedClock(T0)
    q = InMemoryJobQueue(clock, SequentialIdGenerator())

    async def boom(job: Job) -> dict[str, object]:
        raise RuntimeError("nope")

    jid = await q.enqueue(JobSpec(kind="boom", max_attempts=2))
    handlers = {"boom": boom}
    await run_once(q, handlers, "w")  # type: ignore[arg-type]
    assert q.jobs[jid].status == JobStatus.QUEUED and q.jobs[jid].run_at == T0 + timedelta(seconds=5)
    clock.advance(timedelta(seconds=6))
    await run_once(q, handlers, "w")  # type: ignore[arg-type]
    assert q.jobs[jid].status == JobStatus.DEAD


async def test_long_handler_keeps_its_lease_through_heartbeats() -> None:
    import asyncio

    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    queue = InMemoryJobQueue(clock, SequentialIdGenerator(), lease_seconds=300)
    await queue.enqueue(JobSpec(kind="slow"))

    async def slow(job: Job) -> dict[str, JsonValue]:
        for _ in range(3):  # three beats of 0.01 s real time, each advancing the fake clock past a lease
            await asyncio.sleep(0.03)
            clock.advance(timedelta(seconds=200))
            assert await queue.claim("other", ["slow"]) is None
        return {"done": True}

    assert await run_once(queue, {"slow": slow}, "w1", heartbeat_s=0.01)
    assert next(iter(queue.jobs.values())).status is JobStatus.SUCCEEDED
