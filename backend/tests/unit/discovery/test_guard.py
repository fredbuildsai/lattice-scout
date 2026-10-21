"""RunGuard and BudgetedLiterature: reservations, exact charging, caps under concurrency, fencing, cancellation."""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from tests.fakes.discovery import DiscoveryStore, InMemoryLedger

from scout.discovery.application.guard import BudgetedLiterature, RunGuard
from scout.discovery.domain.budget import BudgetExhausted, OpenAlexCapReached, RunBudget, RunSpend
from scout.discovery.domain.run import AnalysisRun, RunCancelled, Superseded
from scout.ports.literature import WorkQuery, WorkRecord
from scout.ports.llm import TokenUsage
from scout.shared import AccountId, FixedClock

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RUN = UUID(int=7)


def _setup(usd: str = "1.00", openalex: int = 10) -> tuple[RunGuard, InMemoryLedger, AnalysisRun]:
    budget = RunBudget(usd=Decimal(usd), openalex_calls=openalex)
    run = AnalysisRun(id=RUN, owner_id=AccountId(UUID(int=1)), created_at=NOW, budget=budget)
    run.generation = 1
    store = DiscoveryStore()
    store.runs[RUN] = run
    ledger = InMemoryLedger(store, FixedClock(NOW))
    return RunGuard(ledger, RUN, 1, budget, RunSpend()), ledger, run


async def test_settled_calls_are_charged_exactly() -> None:
    guard, _, run = _setup()
    async with guard.llm("fast") as call:
        call.settle(Decimal("0.0123"), TokenUsage(input_tokens=100, output_tokens=40))
    assert run.spend.cost_usd == Decimal("0.0123") and run.spend.llm_calls == 1
    assert (run.spend.tokens_in, run.spend.tokens_out) == (100, 40) and guard.spend == run.spend


async def test_a_failed_call_releases_its_reservation_and_is_not_charged() -> None:
    guard, ledger, run = _setup(usd="0.35")
    with pytest.raises(RuntimeError):
        async with guard.llm("reasoning"):
            raise RuntimeError("provider down")
    assert ledger.charges == [] and run.spend.llm_calls == 0
    async with guard.llm("reasoning") as call:  # the reservation is free again
        call.settle(Decimal("0.1"), TokenUsage(input_tokens=1, output_tokens=1))


async def test_parallel_calls_cannot_overshoot_the_budget() -> None:
    guard, _, _ = _setup(usd="0.50")  # one reasoning call (reserve 0.30) fits, two do not
    started = asyncio.Event()

    async def one() -> str:
        try:
            async with guard.llm("reasoning") as call:
                started.set()
                await asyncio.sleep(0.05)
                call.settle(Decimal("0.2"), TokenUsage(input_tokens=1, output_tokens=1))
            return "ok"
        except BudgetExhausted:
            return "budget"

    results = await asyncio.gather(one(), one())
    assert sorted(results) == ["budget", "ok"]


async def test_budget_exhausted_after_spending() -> None:
    guard, _, _ = _setup(usd="0.10")
    async with guard.llm("fast") as call:
        call.settle(Decimal("0.09"), TokenUsage(input_tokens=1, output_tokens=1))
    with pytest.raises(BudgetExhausted):
        async with guard.llm("fast"):
            pytest.fail("must not start")


async def test_openalex_cap_holds_under_concurrency() -> None:
    guard, ledger, run = _setup(openalex=5)
    ok = 0

    async def call() -> None:
        nonlocal ok
        try:
            await guard.openalex()
            ok += 1
        except OpenAlexCapReached:
            pass

    await asyncio.gather(*(call() for _ in range(20)))
    assert ok == 5 and run.spend.openalex_calls == 5 and len(ledger.charges) == 5


async def test_superseded_and_cancelled_runs_stop_at_the_next_call() -> None:
    guard, _, run = _setup()
    run.generation = 2  # another execution took over
    with pytest.raises(Superseded):
        await guard.openalex()
    with pytest.raises(Superseded):
        async with guard.llm("fast") as call:
            call.settle(Decimal("0.01"), TokenUsage(input_tokens=1, output_tokens=1))
    assert run.spend.openalex_calls == 0 and run.spend.llm_calls == 0  # a stale worker adds nothing

    guard2, _, run2 = _setup()
    run2.cancel_requested_at = NOW
    with pytest.raises(RunCancelled):
        await guard2.openalex()


class _Lit:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        self.calls.append("search")
        return []

    async def count_works(self, query: WorkQuery) -> int:
        self.calls.append("count")
        return 7

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        self.calls.append("get")
        return None


async def test_every_literature_call_counts_and_the_cap_stops_the_inner_client() -> None:
    guard, _, run = _setup(openalex=3)
    inner = _Lit()
    lit = BudgetedLiterature(inner, guard)  # type: ignore[arg-type]
    await lit.search_works(WorkQuery(search="x"))
    assert await lit.count_works(WorkQuery(search="x")) == 7
    await lit.get_work("W1")
    with pytest.raises(OpenAlexCapReached):
        await lit.search_works(WorkQuery(search="y"))
    assert inner.calls == ["search", "count", "get"] and run.spend.openalex_calls == 3


async def test_an_invalid_answer_was_paid_for_and_is_charged_at_the_reserve() -> None:
    from scout.ports.llm import LLMOutputInvalid, LLMUnavailable

    guard, _, run = _setup()
    with pytest.raises(LLMOutputInvalid):
        async with guard.llm("reasoning"):
            raise LLMOutputInvalid("bad json")
    assert run.spend.llm_calls == 1 and run.spend.cost_usd == Decimal("0.30")
    with pytest.raises(LLMUnavailable):  # nothing came back: nothing to pay
        async with guard.llm("fast"):
            raise LLMUnavailable("down")
    assert run.spend.llm_calls == 1
