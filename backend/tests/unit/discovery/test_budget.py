"""Run budgets: ``can_start_llm`` / ``can_call_openalex`` with in-flight reservations and ``RunBudget`` validation.

Covers plan M3 task 4 (budgets), DECISIONS 62; design note M3 sections 1 ("budget.py") and 4 ("RunGuard").
"""

from __future__ import annotations

from decimal import Decimal as D
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from scout.discovery.domain.budget import (
    BudgetExhausted,
    OpenAlexCapReached,
    RunBudget,
    RunSpend,
    can_call_openalex,
    can_start_llm,
)

Tier = Literal["fast", "reasoning"]
FAST = D("0.02")
REASONING = D("0.30")


def budget(usd: str = "1.00", calls: int = 400) -> RunBudget:
    return RunBudget(usd=D(usd), openalex_calls=calls)


# ---- can_start_llm ----------------------------------------------------------------------------------------------
def test_defaults_match_the_design_reserves() -> None:
    b = budget()
    assert (b.reserve_fast, b.reserve_reasoning) == (FAST, REASONING)


@pytest.mark.parametrize(("tier", "reserve"), [("fast", FAST), ("reasoning", REASONING)])
def test_the_exact_boundary_is_allowed_and_one_cent_over_is_not(tier: Tier, reserve: D) -> None:
    b = budget("1.00")
    exact = b.usd - reserve  # spend + inflight sits exactly on the limit
    assert can_start_llm(RunSpend(cost_usd=exact), D(0), b, tier) is True
    assert can_start_llm(RunSpend(cost_usd=exact + D("0.01")), D(0), b, tier) is False
    assert can_start_llm(RunSpend(cost_usd=exact - D("0.01")), D(0), b, tier) is True


def test_inflight_reservations_count_against_the_budget() -> None:
    b = budget("1.00")
    spend = RunSpend(cost_usd=D("0.50"))
    # 0.50 spent + 0.18 reserved + 0.30 = 0.98 <= 1.00
    assert can_start_llm(spend, D("0.18"), b, "reasoning") is True
    # exactly on the limit: 0.50 + 0.20 + 0.30
    assert can_start_llm(spend, D("0.20"), b, "reasoning") is True
    assert can_start_llm(spend, D("0.21"), b, "reasoning") is False
    # three reasoning calls in flight leave no room for a fourth, but a fast one still fits
    three = REASONING * 2
    assert can_start_llm(RunSpend(), three, b, "reasoning") is True  # 0.60 + 0.30 = 0.90
    assert can_start_llm(RunSpend(), three + D("0.11"), b, "reasoning") is False
    assert can_start_llm(RunSpend(), three + D("0.11"), b, "fast") is True  # 0.71 + 0.02 = 0.73


def test_a_fast_call_fits_where_a_reasoning_call_does_not() -> None:
    b = budget("0.40")
    spend = RunSpend(cost_usd=D("0.20"))
    assert can_start_llm(spend, D(0), b, "fast") is True
    assert can_start_llm(spend, D(0), b, "reasoning") is False


def test_a_budget_smaller_than_the_reserve_never_starts_a_call() -> None:
    b = budget("0.01")
    assert can_start_llm(RunSpend(), D(0), b, "fast") is False
    assert can_start_llm(RunSpend(), D(0), b, "reasoning") is False


def test_custom_reserves_are_honoured() -> None:
    b = RunBudget(usd=D("1.00"), reserve_fast=D("0.10"), reserve_reasoning=D("0.50"))
    assert can_start_llm(RunSpend(cost_usd=D("0.90")), D(0), b, "fast") is True
    assert can_start_llm(RunSpend(cost_usd=D("0.91")), D(0), b, "fast") is False
    assert can_start_llm(RunSpend(cost_usd=D("0.50")), D(0), b, "reasoning") is True
    assert can_start_llm(RunSpend(cost_usd=D("0.51")), D(0), b, "reasoning") is False


def test_decimal_arithmetic_is_exact_where_floats_are_not() -> None:
    # 0.1 + 0.2 != 0.3 in binary floating point; the budget check must not be fooled by it.
    b = RunBudget(usd=D("0.32"))  # 0.10 + 0.20 + 0.02 == 0.32
    assert can_start_llm(RunSpend(cost_usd=D("0.1")), D("0.2"), b, "fast") is True
    b2 = RunBudget(usd=D("0.319999999"))
    assert can_start_llm(RunSpend(cost_usd=D("0.1")), D("0.2"), b2, "fast") is False
    tiny = RunBudget(usd=D("0.0200000001"))
    assert can_start_llm(RunSpend(), D(0), tiny, "fast") is True
    assert can_start_llm(RunSpend(cost_usd=D("0.00000002")), D(0), tiny, "fast") is False


@given(
    spend=st.decimals(0, 5, places=4),
    inflight=st.decimals(0, 5, places=4),
    extra=st.decimals(0, 3, places=4),
    usd=st.decimals(0, 10, places=4),
    tier=st.sampled_from(["fast", "reasoning"]),
)
def test_more_spend_or_inflight_can_only_make_a_call_less_allowed(
    spend: D, inflight: D, extra: D, usd: D, tier: Tier
) -> None:
    b = RunBudget(usd=usd)
    reserve = b.reserve_fast if tier == "fast" else b.reserve_reasoning
    base = can_start_llm(RunSpend(cost_usd=spend), inflight, b, tier)
    assert base == (spend + inflight + reserve <= usd)
    if not base:
        assert not can_start_llm(RunSpend(cost_usd=spend + extra), inflight, b, tier)
        assert not can_start_llm(RunSpend(cost_usd=spend), inflight + extra, b, tier)


# ---- can_call_openalex ------------------------------------------------------------------------------------------
def test_openalex_cap_counts_calls_in_flight() -> None:
    b = budget(calls=10)
    assert can_call_openalex(RunSpend(openalex_calls=0), 0, b) is True
    assert can_call_openalex(RunSpend(openalex_calls=9), 0, b) is True  # the 10th call
    assert can_call_openalex(RunSpend(openalex_calls=10), 0, b) is False
    assert can_call_openalex(RunSpend(openalex_calls=6), 3, b) is True  # 6 + 3 in flight, one slot left
    assert can_call_openalex(RunSpend(openalex_calls=6), 4, b) is False  # in flight fills the cap
    assert can_call_openalex(RunSpend(openalex_calls=0), 10, b) is False


def test_the_cap_of_one() -> None:
    b = budget(calls=1)
    assert can_call_openalex(RunSpend(), 0, b) is True
    assert can_call_openalex(RunSpend(), 1, b) is False
    assert can_call_openalex(RunSpend(openalex_calls=1), 0, b) is False


@given(spent=st.integers(0, 450), inflight=st.integers(0, 10), cap=st.integers(1, 400))
def test_openalex_rule_is_strictly_below_the_cap(spent: int, inflight: int, cap: int) -> None:
    b = RunBudget(usd=D(1), openalex_calls=cap)
    assert can_call_openalex(RunSpend(openalex_calls=spent), inflight, b) == (spent + inflight < cap)


# ---- RunBudget validation ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("calls", [1, 2, 61, 399, 400])
def test_openalex_calls_inside_the_range_are_accepted(calls: int) -> None:
    assert RunBudget(usd=D(1), openalex_calls=calls).openalex_calls == calls


@pytest.mark.parametrize("calls", [0, -1, 401, 1000])
def test_openalex_calls_outside_1_to_400_are_rejected(calls: int) -> None:
    with pytest.raises(ValueError, match="openalex_calls"):
        RunBudget(usd=D(1), openalex_calls=calls)


def test_default_openalex_calls_is_the_hard_maximum() -> None:
    assert RunBudget(usd=D(1)).openalex_calls == 400


def test_budget_and_spend_are_immutable_value_objects() -> None:
    b = budget()
    with pytest.raises(AttributeError):
        b.usd = D(2)  # type: ignore[misc]
    s = RunSpend()
    assert s == RunSpend(cost_usd=D(0), llm_calls=0, openalex_calls=0, tokens_in=0, tokens_out=0)
    with pytest.raises(AttributeError):
        s.llm_calls = 1  # type: ignore[misc]


def test_budget_errors_are_domain_errors_with_stable_codes() -> None:
    assert BudgetExhausted.code == "budget_exhausted" and BudgetExhausted.http_status == 402
    assert (
        issubclass(OpenAlexCapReached, BudgetExhausted) and OpenAlexCapReached.code == "openalex_cap_reached"
    )
