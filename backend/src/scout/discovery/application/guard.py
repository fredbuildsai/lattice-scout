"""``RunGuard`` and ``BudgetedLiterature``: every model call and every OpenAlex call of a run goes through here.

Spend is charged to the run ledger (exact, crash-safe, fenced by the generation), model calls reserve money while
in flight so parallel calls cannot overshoot, and a lost lease or a cancel request stops the execution at its next
call (DECISIONS 57, 62).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Literal
from uuid import UUID

from scout.discovery.domain.budget import (
    BudgetExhausted,
    OpenAlexCapReached,
    RunBudget,
    RunSpend,
    can_call_openalex,
    can_start_llm,
)
from scout.discovery.domain.run import RunCancelled, Superseded
from scout.discovery.ports.ledger import LedgerState, RunLedgerPort
from scout.ports.literature import LiteraturePort, WorkQuery, WorkRecord
from scout.ports.llm import LLMOutputInvalid, TokenUsage

Tier = Literal["fast", "reasoning"]


class LlmTicket:
    """One reserved model call. ``settle`` charges the actual cost; leaving the block without it releases the
    reservation and charges only the call itself."""

    def __init__(self) -> None:
        self.cost = Decimal(0)
        self.usage: TokenUsage | None = None
        self.settled = False

    def settle(self, cost_usd: Decimal, usage: TokenUsage) -> None:
        """Record the actual cost and token counts of the finished call."""
        self.cost, self.usage, self.settled = cost_usd, usage, True


class RunGuard:
    """One per execution. Not shared between runs or executions."""

    def __init__(
        self, ledger: RunLedgerPort, run_id: UUID, generation: int, budget: RunBudget, spend: RunSpend
    ) -> None:
        self._ledger, self._run_id, self._generation, self._budget = ledger, run_id, generation, budget
        self.spend = spend
        self._inflight_usd = Decimal(0)
        self._inflight_openalex = 0
        self._lock = asyncio.Lock()

    def _apply(self, state: LedgerState) -> None:
        self.spend = state.spend
        if state.superseded:
            raise Superseded("A newer execution owns this run")
        if state.cancel_requested:
            raise RunCancelled("The run was cancelled")

    @asynccontextmanager
    async def llm(self, tier: Tier) -> AsyncIterator[LlmTicket]:
        """Reserve money for one model call.

        Raises:
            BudgetExhausted: the call does not fit the remaining budget.
            Superseded, RunCancelled: raised after the call, from the ledger state.
        """
        reserve = self._budget.reserve_fast if tier == "fast" else self._budget.reserve_reasoning
        async with self._lock:
            if not can_start_llm(self.spend, self._inflight_usd, self._budget, tier):
                raise BudgetExhausted("The run budget does not allow another model call")
            self._inflight_usd += reserve
        ticket = LlmTicket()
        try:
            yield ticket
        except BaseException as exc:
            paid = ticket.settled or isinstance(exc, LLMOutputInvalid)  # an invalid answer was still paid for
            if isinstance(exc, LLMOutputInvalid) and not ticket.settled:
                ticket.cost = reserve
            if paid:
                try:
                    await self._settle(ticket, reserve)
                except (Superseded, RunCancelled):
                    pass  # the original error is the one to surface
            else:
                await self._release(reserve)
            raise
        await self._settle(ticket, reserve)

    async def _release(self, reserve: Decimal) -> None:
        async with self._lock:
            self._inflight_usd -= reserve

    async def _settle(self, ticket: LlmTicket, reserve: Decimal) -> None:
        """Charge the call, then release its reservation and apply the new totals in one step."""
        try:
            state = await self._ledger.charge(
                self._run_id, self._generation, cost_usd=ticket.cost, llm_calls=1,
                tokens_in=ticket.usage.input_tokens if ticket.usage else 0,
                tokens_out=ticket.usage.output_tokens if ticket.usage else 0,
            )  # fmt: skip
        except BaseException:
            await self._release(reserve)
            raise
        async with self._lock:
            self._inflight_usd -= reserve
            self.spend = state.spend
        self._apply(state)

    async def openalex(self) -> None:
        """Count one OpenAlex call *before* it is made.

        Raises:
            OpenAlexCapReached: the run's cap is used up.
            Superseded, RunCancelled: from the ledger state.
        """
        async with self._lock:
            if not can_call_openalex(self.spend, self._inflight_openalex, self._budget):
                raise OpenAlexCapReached("The run reached its OpenAlex call cap")
            self._inflight_openalex += 1
        try:
            state = await self._ledger.charge(self._run_id, self._generation, openalex_calls=1)
        finally:
            async with self._lock:
                self._inflight_openalex -= 1
        self._apply(state)


class BudgetedLiterature:
    """The three literature calls a run may make, each counted against the run's OpenAlex cap.

    Every port call counts, cache hits included (the application cannot see them): conservative on purpose. One
    instance per execution, so the shared client keeps no per-run counter (DECISIONS 39).
    """

    def __init__(self, inner: LiteraturePort, guard: RunGuard) -> None:
        self._inner, self._guard = inner, guard

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        """Counted ``search_works``."""
        await self._guard.openalex()
        return await self._inner.search_works(query)

    async def count_works(self, query: WorkQuery) -> int:
        """Counted ``count_works``."""
        await self._guard.openalex()
        return await self._inner.count_works(query)

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        """Counted ``get_work``."""
        await self._guard.openalex()
        return await self._inner.get_work(work_id_or_doi)
