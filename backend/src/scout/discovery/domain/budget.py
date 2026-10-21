"""Run budgets and spend (pure)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from scout.shared import DomainError


class BudgetExhausted(DomainError):
    """The run's money budget does not allow another model call."""

    code = "budget_exhausted"
    http_status = 402
    title = "Run budget exhausted"


class OpenAlexCapReached(BudgetExhausted):
    """The run's OpenAlex call cap was reached."""

    code = "openalex_cap_reached"
    title = "OpenAlex call cap reached"


@dataclass(frozen=True)
class RunBudget:
    """Limits of one run."""

    usd: Decimal
    openalex_calls: int = 400
    max_corpus: int = 400
    reserve_fast: Decimal = Decimal("0.02")
    reserve_reasoning: Decimal = Decimal("0.30")

    def __post_init__(self) -> None:
        if not 1 <= self.openalex_calls <= 400:
            raise ValueError("openalex_calls must be between 1 and 400")


@dataclass(frozen=True)
class RunSpend:
    """Exact totals of a run, kept in the database by the ledger."""

    cost_usd: Decimal = Decimal(0)
    llm_calls: int = 0
    openalex_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def can_start_llm(
    spend: RunSpend, inflight_reserved: Decimal, budget: RunBudget, tier: Literal["fast", "reasoning"]
) -> bool:
    """Whether another model call may start: spend, calls in flight and this call's reserve must fit the budget."""
    reserve = budget.reserve_fast if tier == "fast" else budget.reserve_reasoning
    return spend.cost_usd + inflight_reserved + reserve <= budget.usd


def can_call_openalex(spend: RunSpend, inflight: int, budget: RunBudget) -> bool:
    """Whether another OpenAlex call fits the cap (calls in flight count)."""
    return spend.openalex_calls + inflight < budget.openalex_calls
