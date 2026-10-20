"""JobQueuePort contract. Parametrised over implementations (Postgres added in M0.6)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.fakes import InMemoryJobQueue

from scout.ports.jobs import JobQueuePort, JobSpec, JobStatus
from scout.shared import FixedClock, SequentialIdGenerator

T0 = datetime(2026, 1, 1, tzinfo=UTC)
Factory = Callable[[FixedClock], Any]


def _memory(clock: FixedClock) -> JobQueuePort:
    return InMemoryJobQueue(clock, SequentialIdGenerator(), lease_seconds=300)


FACTORIES = [pytest.param(_memory, id="memory")]


@pytest.fixture(params=FACTORIES)
def make(request: pytest.FixtureRequest) -> Callable[[FixedClock], JobQueuePort]:
    return request.param  # type: ignore[no-any-return]


async def test_claim_once_and_attempts(make: Callable[[FixedClock], JobQueuePort]) -> None:
    clock = FixedClock(T0)
    q = make(clock)
    jid = await q.enqueue(JobSpec(kind="ping"))
    job = await q.claim("w1", ["ping"])
    assert job is not None and job.id == jid and job.attempts == 1 and job.status == JobStatus.RUNNING
    assert await q.claim("w2", ["ping"]) is None


async def test_future_and_kind_filter(make: Callable[[FixedClock], JobQueuePort]) -> None:
    clock = FixedClock(T0)
    q = make(clock)
    await q.enqueue(JobSpec(kind="later", run_at=T0 + timedelta(minutes=1)))
    await q.enqueue(JobSpec(kind="other"))
    assert await q.claim("w", ["later"]) is None
    clock.advance(timedelta(minutes=2))
    assert (await q.claim("w", ["later"])) is not None


async def test_fail_retry_and_dead(make: Callable[[FixedClock], JobQueuePort]) -> None:
    clock = FixedClock(T0)
    q = make(clock)
    await q.enqueue(JobSpec(kind="k", max_attempts=2))
    job = await q.claim("w", ["k"])
    assert job
    await q.fail(job.id, "boom", retry_in=10)
    assert await q.claim("w", ["k"]) is None
    clock.advance(timedelta(seconds=11))
    job = await q.claim("w", ["k"])
    assert job and job.attempts == 2
    await q.fail(job.id, "boom", retry_in=10)  # attempts exhausted -> dead
    clock.advance(timedelta(hours=1))
    assert await q.claim("w", ["k"]) is None


async def test_lease_expiry_and_dedupe(make: Callable[[FixedClock], JobQueuePort]) -> None:
    clock = FixedClock(T0)
    q = make(clock)
    a = await q.enqueue(JobSpec(kind="k", dedupe_key="x"))
    assert await q.enqueue(JobSpec(kind="k", dedupe_key="x")) == a
    assert await q.claim("w1", ["k"])
    clock.advance(timedelta(seconds=301))
    again = await q.claim("w2", ["k"])
    assert again and again.attempts == 2 and again.locked_by == "w2"
