from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.fakes import InMemoryUnitOfWork

from scout.ports.jobs import retry_delay
from scout.shared import (
    AggregateRoot,
    DomainEvent,
    FixedClock,
    SequentialIdGenerator,
    SystemClock,
    UuidGenerator,
    ValidationFailed,
    errors,
    parse_id,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_clocks_are_aware_utc() -> None:
    assert SystemClock().now().utcoffset() == timedelta(0)
    c = FixedClock(T0)
    c.advance(timedelta(hours=1))
    assert c.now() == T0 + timedelta(hours=1)


def test_fixed_clock_rejects_naive_and_negative() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        FixedClock(T0).advance(timedelta(seconds=-1))


def test_ids() -> None:
    g = SequentialIdGenerator()
    assert [g.new(), g.new()] == [UUID(int=1), UUID(int=2)]
    assert UuidGenerator().new().version == 4
    with pytest.raises(ValidationFailed):
        parse_id("nope")


def test_error_codes_unique_and_statuses_valid() -> None:
    classes = [c for c in vars(errors).values() if isinstance(c, type) and issubclass(c, errors.ScoutError)]
    assert len({c.code for c in classes}) == len(classes)
    assert all(400 <= c.http_status <= 599 for c in classes)
    assert all(c.http_status == 409 for c in classes if issubclass(c, errors.Conflict))
    p = errors.NotFound("x", extra={"id": "1"}).to_problem()
    assert p == {
        "type": "/problems/not_found",
        "title": "Not found",
        "status": 404,
        "detail": "x",
        "code": "not_found",
        "id": "1",
    }


class Happened(DomainEvent):
    name = "test.happened"


def test_events_frozen_and_pulled_in_order() -> None:
    e1 = Happened(event_id=UUID(int=1), occurred_at=T0)
    e2 = Happened(event_id=UUID(int=2), occurred_at=T0)
    with pytest.raises(ValidationError):
        Happened(event_id=UUID(int=3), occurred_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        e1.event_id = UUID(int=9)  # type: ignore[misc]
    agg = AggregateRoot()
    agg.record_event(e1)
    agg.record_event(e2)
    assert agg.pull_events() == [e1, e2]
    assert agg.pull_events() == []


async def test_in_memory_uow_rollback_and_commit() -> None:
    store: dict[str, object] = {}
    async with InMemoryUnitOfWork(store) as uow:
        uow.put("a", 1)
    assert store == {}
    with pytest.raises(RuntimeError):
        async with InMemoryUnitOfWork(store) as uow:
            uow.put("a", 1)
            await uow.commit()
            raise RuntimeError
    assert store == {"a": 1}


def test_retry_delay_monotonic_capped() -> None:
    d = [retry_delay(i) for i in range(1, 12)]
    assert d == sorted(d) and d[0] == 5 and d[-1] == 600
