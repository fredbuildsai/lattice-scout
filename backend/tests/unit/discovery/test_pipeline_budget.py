"""Budgets: every step degrades as documented and the caps are never exceeded (plan M3 task 4; design M3 sections 2.2
"Budget or cap hit" column and 4, DECISIONS 62).

A run cannot be *started* with a budget this small (``StartAnalysisRun`` refuses below 0.36 USD), so the tests
shrink the stored run's budget with ``World.set_budget``. The fake agents cost 0.01 USD per call; reservations are
0.02 (fast tier: QueryPlanner, ClusterLabeler, InsightTopicTagger) and 0.30 (reasoning tier: GapAnalyst, Critic).
A clean run makes 8 model calls (1 + 3 + 1 + 1 + 2) and 4 OpenAlex calls (2 searches, 2 counts).

Budget arithmetic used below (a call starts when ``spent + reserve <= budget``):

* 0.010: the QueryPlanner cannot start.
* 0.025: QueryPlanner runs (spent 0.01); no fast call fits afterwards.
* 0.055: QueryPlanner and the three labellers run (spent 0.04); the tagger does not fit.
* 0.200: all five fast calls run (spent 0.05); no reasoning call fits.
* 0.355: GapAnalyst runs (spent 0.06); the Critic does not fit.
* 0.365: GapAnalyst and one Critic run (spent 0.07); the second Critic does not fit.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.fakes.discovery import AgentKilled
from tests.unit.discovery._world import (
    World,
    assert_user_view_clean,
    claim,
    count_ref,
    draft,
    plan_n,
    script_drafts,
    work_ref,
)

from scout.discovery.application.start import StartConfig
from scout.discovery.domain.enums import ClaimStatus, OppStatus, RunFailure, RunStatus, RunStep


async def run_with_budget(usd: str, **budget: object) -> tuple[World, dict[str, object]]:
    w = World()
    run = await w.start()
    w.set_budget(run, usd=Decimal(usd), **budget)
    result = await w.run(run)
    return w, dict(result)


def degraded(w: World) -> set[tuple[str, str]]:
    [run] = w.store.runs.values()
    return {(d.step.value, d.reason) for d in run.degraded}


# ---- money: one test per model-calling step ---------------------------------------------------------------------------
async def test_query_planner_without_budget_fails_the_run_with_budget_exhausted() -> None:
    w, result = await run_with_budget("0.010")
    [run] = w.store.runs.values()
    assert result == {"failed": "budget_exhausted"}
    assert run.status is RunStatus.FAILED
    assert run.error_code == RunFailure.BUDGET_EXHAUSTED.value and run.error_step is RunStep.PLAN_QUERIES
    assert w.agents.calls == [] and w.literature.calls == []  # nothing was produced, nothing was spent
    assert run.spend.cost_usd == 0 and run.spend.llm_calls == 0
    assert not w.opps(run)


async def test_cluster_labeller_without_budget_falls_back_to_topic_names() -> None:
    w, result = await run_with_budget("0.025")
    [run] = w.store.runs.values()
    assert result["status"] == "done" and run.partial
    assert (RunStep.GROUP_BY_TOPIC.value, "label_fallback") in degraded(w)
    assert w.agents.count("ClusterLabeler") == 0
    clusters = list(w.store.clusters[run.id].values())
    assert clusters and all(c.label_source == "topic" for c in clusters)
    assert {c.label for c in clusters if not c.is_misc} <= {
        "Topic T1",
        "Topic T2",
        "Topic T3",
    }  # named after the topic
    assert all(c.label.strip() for c in clusters)
    assert run.step_state[RunStep.GROUP_BY_TOPIC].status.value == "degraded"


async def test_tagger_without_budget_is_skipped_and_coverage_uses_works_only() -> None:
    w, result = await run_with_budget("0.055")
    [run] = w.store.runs.values()
    assert result["status"] == "done" and run.partial
    assert (RunStep.COVERAGE.value, "tags_skipped") in degraded(w)
    assert w.agents.count("ClusterLabeler") == 3 and w.agents.count("InsightTopicTagger") == 0
    assert all(c.label_source == "llm" for c in w.store.clusters[run.id].values() if not c.is_misc)
    assert all(c.insight_weight == 0 for c in w.store.clusters[run.id].values())
    assert any(
        c.candidate_rank is not None for c in w.store.clusters[run.id].values()
    )  # still picks candidates


async def test_analyst_without_budget_ends_done_and_partial_with_nothing_shown() -> None:
    w, result = await run_with_budget("0.200")
    [run] = w.store.runs.values()
    assert result["status"] == "done" and run.status is RunStatus.DONE and run.percent() == 100
    assert run.partial and (RunStep.ANALYSE.value, "analyse_truncated") in degraded(w)
    assert w.agents.count("GapAnalyst") == 0 and w.agents.count("Critic") == 0
    assert not w.opps(run) and not w.shown(run)


async def test_critic_without_budget_leaves_the_claims_unverified_with_budget_exhausted() -> None:
    w, result = await run_with_budget("0.355")
    [run] = w.store.runs.values()
    assert result["status"] == "done" and run.partial
    assert w.agents.count("GapAnalyst") == 1 and w.agents.count("Critic") == 0
    assert any(step == "verify" and "budget_exhausted" in reason for step, reason in degraded(w))
    opps = w.opps(run)
    assert opps and not w.shown(run)
    for o in opps:
        assert o.status is OppStatus.UNVERIFIED and o.status_reason == "budget_exhausted"
        assert o.claims, "drafts are persisted, never deleted"
        assert all(
            c.status is ClaimStatus.UNVERIFIED and c.status_reason == "budget_exhausted" for c in o.claims
        )
        assert o.visible_claims() == []
    assert_user_view_clean(w.store)


async def test_the_budget_running_out_between_two_critic_calls_judges_one_draft_and_parks_the_other() -> None:
    w, _ = await run_with_budget("0.365")
    [run] = w.store.runs.values()
    assert w.agents.count("Critic") == 1
    first, second = w.opps(run)
    assert first.status is OppStatus.SHOWN and first.show_decision()[0]
    assert second.status is OppStatus.UNVERIFIED and second.status_reason == "budget_exhausted"
    assert all(c.status is ClaimStatus.UNVERIFIED for c in second.claims) and second.claims
    assert run.partial and run.spend.cost_usd <= run.budget.usd
    assert_user_view_clean(w.store)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(milli=st.integers(min_value=0, max_value=450))
def test_spend_never_exceeds_the_budget_and_a_shortfall_is_never_silent(milli: int) -> None:
    """For any budget: spent <= budget, and fewer than the 8 calls of a clean run means ``partial`` (or failed)."""
    usd = Decimal(milli) / 1000

    async def scenario() -> None:
        w, _ = await run_with_budget(str(usd))
        [run] = w.store.runs.values()
        assert run.spend.cost_usd <= usd
        assert run.status in (RunStatus.DONE, RunStatus.FAILED)
        if run.status is RunStatus.FAILED:
            assert run.error_code == RunFailure.BUDGET_EXHAUSTED.value and usd < Decimal("0.02")
        elif run.spend.llm_calls < 8:
            assert run.partial and run.degraded
        else:
            assert not run.partial
        assert_user_view_clean(w.store)

    asyncio.run(scenario())


# ---- OpenAlex calls ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("cap", range(1, 10))
async def test_the_openalex_cap_is_never_exceeded_and_a_hit_is_reported(cap: int) -> None:
    """Six queries need 6 searches + 2 counts = 8 port calls; with a smaller cap the run degrades, never overshoots."""
    w = World(start_cfg=StartConfig(openalex_calls=cap))
    w.agents.script("QueryPlanner", plan_n(6))
    run = await w.start()
    assert run.budget.openalex_calls == cap
    result = await w.run(run)
    final = w.get(run)
    assert result["status"] == "done", result
    assert final.spend.openalex_calls <= cap
    assert len(w.literature.calls) <= cap  # what actually reached OpenAlex
    assert final.spend.openalex_calls == len(w.literature.calls)  # charged before each call, no more no less
    hit = {(d.step.value, d.reason) for d in final.degraded}
    if cap < 8:
        assert final.partial and any(reason == "openalex_cap" for _, reason in hit)
    else:
        assert not final.partial and not any(reason == "openalex_cap" for _, reason in hit)
    assert_user_view_clean(w.store)


@pytest.mark.parametrize("cap", [1, 2, 3, 4, 5])
async def test_a_cap_hit_in_retrieve_keeps_the_corpus_and_degrades_the_step(cap: int) -> None:
    w = World(start_cfg=StartConfig(openalex_calls=cap))
    w.agents.script("QueryPlanner", plan_n(6))
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert ("retrieve", "openalex_cap") in {(d.step.value, d.reason) for d in final.degraded}
    assert final.step_state[RunStep.RETRIEVE].status.value == "degraded"
    searched = [x for x in w.store.corpus[run.id].values() if x.origin.value == "search"]
    assert searched, "the works found before the cap are kept"
    assert len(w.literature.search_log) == min(cap, 6)


async def test_a_cap_of_five_with_the_default_plan_is_not_hit() -> None:
    w = World(start_cfg=StartConfig(openalex_calls=5))
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert final.spend.openalex_calls == 4 and not final.partial and w.shown(run)


async def test_a_cap_hit_while_measuring_novelty_drops_unmeasured_candidates() -> None:
    w = World(start_cfg=StartConfig(openalex_calls=2))  # both searches, no count
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.spend.openalex_calls == 2 == len(w.literature.calls)
    assert ("measure_novelty", "openalex_cap") in {(d.step.value, d.reason) for d in final.degraded}
    assert all(p.status == "skipped" and p.count is None for p in w.store.probes[run.id].values())
    assert w.agents.count("GapAnalyst") == 0  # a candidate without a measured count is never analysed
    assert not w.opps(run) and not w.shown(run)


async def test_a_cap_hit_while_resolving_a_cited_work_leaves_the_citation_unresolved() -> None:
    w = World(start_cfg=StartConfig(openalex_calls=4))  # searches and counts use all four
    script_drafts(
        w,
        draft(
            "Hydrophobic membrane coatings for packaging",
            [
                claim(
                    "A work outside the corpus supports it.", work_ref("W13", "Study 13 reports that lignin")
                ),
                claim("Measured literature volume is small.", count_ref()),
            ],
        ),
    )
    w.literature.known["W13"] = w.literature.known["W12"]
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.spend.openalex_calls == 4 == len(w.literature.calls)
    assert "get" not in w.literature.calls  # the cap stopped the lookup before it reached OpenAlex
    [opp] = w.opps(run)
    unresolved = opp.claims[0].evidence[0]
    assert (
        unresolved.verified is False
        and unresolved.check is not None
        and unresolved.check.value == "unknown_work"
    )
    assert opp.claims[0].status is not ClaimStatus.VERIFIED
    assert not w.shown(run)


# ---- corpus -----------------------------------------------------------------------------------------------------------
async def test_zero_search_results_fail_the_run_with_corpus_empty() -> None:
    w = World()
    w.literature.works = []
    run = await w.start()
    result = await w.run(run)
    final = w.get(run)
    assert result == {"failed": "corpus_empty"}
    assert final.status is RunStatus.FAILED and final.error_code == "corpus_empty"
    assert final.error_step is RunStep.RETRIEVE
    assert w.agents.count("QueryPlanner") == 1 and w.agents.count("ClusterLabeler") == 0  # stops right there
    assert not w.opps(run)


async def test_the_corpus_cap_limits_search_works_but_not_the_researchers_own_works() -> None:
    w = World()
    run = await w.start()
    w.set_budget(run, max_corpus=5)
    await w.run(run)
    works = w.store.corpus[run.id].values()
    assert sum(1 for x in works if x.origin.value == "search") == 5
    assert sum(1 for x in works if x.origin.value == "profile") == 2
    assert w.get(run).status is RunStatus.DONE


# ---- budgets span executions ------------------------------------------------------------------------------------------
async def test_the_money_budget_counts_both_executions_of_a_resumed_run() -> None:
    w = World()
    run = await w.start()
    w.set_budget(run, usd=Decimal("0.365"))  # GapAnalyst and one Critic fit; the second Critic does not
    w.agents.kill_on_call = 6  # the first execution dies in the GapAnalyst call (never charged)
    with pytest.raises(AgentKilled):
        await w.run(run)
    w.agents.kill_on_call = None
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.partial
    assert w.agents.count("Critic") == 1
    assert (
        final.spend.cost_usd == Decimal("0.07") <= final.budget.usd
    )  # 5 fast + analyst + one critic, killed call free
    first, second = w.opps(run)
    assert first.status is OppStatus.SHOWN and second.status_reason == "budget_exhausted"


async def test_the_openalex_cap_counts_both_executions_of_a_resumed_run() -> None:
    w = World(start_cfg=StartConfig(openalex_calls=5))
    w.agents.script("QueryPlanner", plan_n(6))
    run = await w.start()
    w.literature.kill_on = ("search", 5)  # dies on the fifth (and last permitted) search
    with pytest.raises(AgentKilled):
        await w.run(run)
    w.literature.kill_on = None
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE
    assert (
        final.spend.openalex_calls == 5 == len(w.literature.calls)
    )  # the restart did not get a fresh allowance
    assert any(d.reason == "openalex_cap" for d in final.degraded)
