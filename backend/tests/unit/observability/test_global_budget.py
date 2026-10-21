"""M6 slice B: the global daily budget guard (plan M6 task 3; acceptance: exceeding the daily budget pauses new runs)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from scout.adapters.observability.budget import GlobalBudget
from scout.ports.budget import ServicePaused
from scout.shared import FixedClock

NOON = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


class Reader:
    def __init__(self, llm: str = "0", oa: str = "0") -> None:
        self.llm, self.oa = Decimal(llm), Decimal(oa)
        self.since: list[datetime] = []

    async def spent_since(self, since: datetime) -> tuple[Decimal, Decimal]:
        self.since.append(since)
        return self.llm, self.oa


def _guard(reader: Reader, ttl: float = 0.0) -> GlobalBudget:
    return GlobalBudget(reader, FixedClock(NOON), Decimal(10), Decimal(5), ttl_s=ttl)


async def test_open_below_the_caps_and_reads_since_utc_midnight() -> None:
    r = Reader("3", "1")
    g = _guard(r)
    await g.ensure_open()
    st = await g.status()
    assert not st.paused and st.llm_spent_usd == 3
    assert r.since[0] == datetime(2026, 10, 24, tzinfo=UTC)


@pytest.mark.parametrize(("llm", "oa"), [("10", "0"), ("11.5", "0"), ("0", "5")])
async def test_either_cap_pauses_new_runs(llm: str, oa: str) -> None:
    g = _guard(Reader(llm, oa))
    with pytest.raises(ServicePaused) as info:
        await g.ensure_open()
    assert info.value.code == "service_paused" and info.value.http_status == 503


async def test_alerts_fire_once_per_service_and_day() -> None:
    r = Reader("8.5", "0")
    g = _guard(r)
    with capture_logs() as logs:
        await g.status()
        await g.status()
        r.llm = Decimal(10)
        await g.status()
        await g.status()
    events = [(e["event"], e["service"]) for e in logs]
    assert events == [("budget.warning", "llm"), ("budget.cap_reached", "llm")]


async def test_status_is_cached_for_the_ttl() -> None:
    r = Reader("1")
    g = _guard(r, ttl=60)
    await g.status()
    await g.status()
    assert len(r.since) == 1


async def test_a_new_day_starts_with_a_fresh_window() -> None:
    clock = FixedClock(NOON)
    r = Reader("10")
    g = GlobalBudget(r, clock, Decimal(10), Decimal(5), ttl_s=0)
    assert (await g.status()).paused
    clock.advance(timedelta(hours=13))
    r.llm = Decimal(0)
    assert not (await g.status()).paused
    assert r.since[-1] == datetime(2026, 10, 25, tzinfo=UTC)
