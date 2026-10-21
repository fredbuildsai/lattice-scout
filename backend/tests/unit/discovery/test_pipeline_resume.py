"""Resume after a kill: the run job survives a dying worker (plan M3 task 3, A2; design M3 sections 2.1 and 8).

Covers: "Killing the worker mid-run and restarting resumes from the last completed step", the resume rule column of
the step table (design 2.2), and "each completed unit is done once" (QueryPlanner x1 overall, no duplicate rows,
a ledger that includes both executions).

A kill is simulated three ways, all with fakes only:
* mid-step, on the n-th agent call (``FakeDiscoveryAgents.kill_on_call``) or the n-th literature call
  (``ScriptedLiterature.kill_on``): the call raises ``AgentKilled`` (a ``BaseException``, so no ``except Exception``
  in the pipeline can swallow it);
* at the step boundary, by losing the commit that completes a step (``kill_at_boundary``): every output of the step
  is already persisted, only ``complete_step`` is missing. This is the only way to kill step 8, which makes no call;
* repeatedly, killing every execution (Hypothesis picks the kill points).

The restart is a *new* ``RunAnalysis`` (``World.runner`` builds one per access) over the same store.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.fakes.discovery import AgentKilled
from tests.unit.discovery._world import (
    STEP_CALLS,
    STEPS,
    World,
    assert_user_view_clean,
    call_names,
    finished_world,
    fingerprint,
    kill_at_boundary,
    plan_n,
    row_ids,
    src_bug,
)

from scout.discovery.domain.enums import RunStatus, RunStep

COST = Decimal("0.01")


@dataclass(frozen=True)
class Kill:
    """Where the first execution dies."""

    step: RunStep
    agent_call: int | None = None  # n-th agent call overall, from 1
    lit: tuple[str, int] | None = None  # n-th literature call of that kind, from 1
    queries: int = 2  # queries the QueryPlanner emits (6 gives two retrieve waves)


# Baseline agent call order: 1 QueryPlanner, 2-4 ClusterLabeler x3, 5 InsightTopicTagger, 6 GapAnalyst, 7-8 Critic x2.
MIDSTEP = {
    "plan_queries@QueryPlanner": Kill(RunStep.PLAN_QUERIES, agent_call=1),
    "retrieve@first-search": Kill(RunStep.RETRIEVE, lit=("search", 1)),
    "retrieve@second-wave": Kill(RunStep.RETRIEVE, lit=("search", 5), queries=6),
    "group_by_topic@first-label": Kill(RunStep.GROUP_BY_TOPIC, agent_call=2),
    "group_by_topic@second-label": Kill(RunStep.GROUP_BY_TOPIC, agent_call=3),
    "coverage@tagger": Kill(RunStep.COVERAGE, agent_call=5),
    "measure_novelty@first-count": Kill(RunStep.MEASURE_NOVELTY, lit=("count", 1)),
    "measure_novelty@second-count": Kill(RunStep.MEASURE_NOVELTY, lit=("count", 2)),
    "analyse@GapAnalyst": Kill(RunStep.ANALYSE, agent_call=6),
    "verify@first-critic": Kill(RunStep.VERIFY, agent_call=7),
    "verify@second-critic": Kill(RunStep.VERIFY, agent_call=8),
}


def build(queries: int = 2) -> World:
    w = World()
    if queries != 2:
        w.agents.script("QueryPlanner", plan_n(queries))
    return w


async def killed_and_resumed(k: Kill) -> tuple[World, World, Counter[str], list[str]]:
    """Run once with a kill, then again; return (resumed world, clean world, completed agent calls, resumed names)."""
    clean, _ = await finished_world(lambda w: w.agents.script("QueryPlanner", plan_n(k.queries)) if k.queries != 2 else None)
    w = build(k.queries)
    run = await w.start()
    w.agents.kill_on_call = k.agent_call
    w.literature.kill_on = k.lit
    with pytest.raises(AgentKilled):
        await w.run(run)
    mid = w.get(run)
    assert mid.status is RunStatus.RUNNING
    assert mid.step is k.step, "the kill must land in the step under test"
    assert mid.generation == 1
    completed = Counter(a for a, _ in w.agents.calls)
    if k.agent_call is not None:
        completed[w.agents.calls[k.agent_call - 1][0]] -= 1  # the killed call produced nothing
    n_agents, n_lit = len(w.agents.calls), len(w.literature.calls)
    w.agents.kill_on_call, w.literature.kill_on = None, None
    result = await w.run(run)
    assert result["status"] == "done", result
    return w, clean, completed + Counter(a for a, _ in w.agents.calls[n_agents:]), call_names(w, n_agents, n_lit)


@pytest.mark.parametrize("case", MIDSTEP.values(), ids=list(MIDSTEP))
async def test_a_midstep_kill_resumes_to_done_and_repeats_no_completed_unit(case: Kill) -> None:
    w, clean, completed, resumed = await killed_and_resumed(case)
    [run] = w.store.runs.values()
    [clean_run] = clean.store.runs.values()
    assert run.status is RunStatus.DONE and run.percent() == 100
    assert run.generation == 2 and run.executions == 2  # the restart took a new fencing generation
    # nothing before the killed step runs again
    allowed = set().union(*(STEP_CALLS[s] for s in STEPS[STEPS.index(case.step) :]))
    assert set(resumed) <= allowed, resumed
    # every completed model call happened exactly as often as in an uninterrupted run (QueryPlanner x1 overall)
    assert completed == Counter(a for a, _ in clean.agents.calls)
    assert completed["QueryPlanner"] == 1
    # no row lost, none duplicated
    assert row_ids(w.store, run.id) == row_ids(clean.store, clean_run.id)
    # the ledger holds both executions: completed calls only, the killed one never settled
    assert run.spend.llm_calls == sum(completed.values())
    assert run.spend.cost_usd == COST * run.spend.llm_calls
    assert run.spend.openalex_calls == len(w.literature.calls)  # charged before each call, killed one included
    assert_user_view_clean(w.store)


async def test_a_kill_in_the_second_retrieve_wave_keeps_the_first_wave() -> None:
    w, _, _, resumed = await killed_and_resumed(MIDSTEP["retrieve@second-wave"])
    assert [n for n in resumed if n == "search"] == ["search", "search"]  # only q5 and q6
    first_four = w.literature.search_log[:4]
    assert w.literature.search_log[4:6] == w.literature.search_log[6:8]
    assert all(w.literature.search_log.count(q) == 1 for q in first_four)
    [run] = w.store.runs.values()
    assert run.step_state[RunStep.RETRIEVE].data["done"] == ["q1", "q2", "q3", "q4", "q5", "q6"]


@pytest.mark.parametrize("n", [1, 2])
async def test_a_kill_while_counting_recounts_only_the_unfinished_wave(n: int) -> None:
    key = "measure_novelty@first-count" if n == 1 else "measure_novelty@second-count"
    _, _, _, resumed = await killed_and_resumed(MIDSTEP[key])
    assert [x for x in resumed if x == "count"] == ["count", "count"]  # one wave of two probes, none committed


async def test_a_kill_after_the_first_critic_does_not_repeat_it() -> None:
    w, _, _, resumed = await killed_and_resumed(MIDSTEP["verify@second-critic"])
    assert [x for x in resumed if x == "Critic"] == ["Critic"]
    assert w.agents.count("Critic") == 3  # first, the killed second, the retry


@pytest.mark.parametrize("step", STEPS, ids=[s.value for s in STEPS])
async def test_a_kill_at_the_step_boundary_repeats_nothing(step: RunStep) -> None:
    clean, _ = await finished_world()
    w = World()
    kill_at_boundary(w, step)
    run = await w.start()
    with pytest.raises(AgentKilled):
        await w.run(run)
    mid = w.get(run)
    assert mid.status is RunStatus.RUNNING and mid.step is step  # outputs persisted, step not completed
    assert step not in {s for s, r in mid.step_state.items() if r.finished_at is not None}
    w.store.before_commit = None
    result = await w.run(run)
    final = w.get(run)
    assert result["status"] == "done" and final.status is RunStatus.DONE
    assert final.generation == 2
    # the persisted outputs are reused: not one extra model or literature call
    assert call_names(w) == call_names(clean)
    assert w.agents.count("QueryPlanner") == 1
    assert row_ids(w.store, final.id) == row_ids(clean.store, final.id)
    assert final.spend.llm_calls == len(w.agents.calls)
    assert final.spend.cost_usd == COST * len(w.agents.calls)
    assert final.spend.openalex_calls == len(w.literature.calls)
    assert_user_view_clean(w.store)


# ---- the resumed run ends exactly like an uninterrupted one (strict xfail where src differs) -----------------------
BOUNDARY_BUGS = {
    RunStep.GROUP_BY_TOPIC: src_bug(
        1,
        "group_by_topic derives label_fallback from len(todo), which shrinks on resume; a fully labelled run "
        "is flagged degraded/partial (steps_retrieval.py:344). Use the clusters still labelled 'topic' instead",
    ),
    RunStep.SCORE_PERSIST: src_bug(
        2,
        "score_persist re-run reports shown=0 in its summary because only draft opportunities are re-scored "
        "(steps_analysis.py:663/690). Count shown from the saved statuses",
    ),
    RunStep.VERIFY: src_bug(
        3,
        "verify re-run reports checked=0 / no outcome counts in its summary because only pending drafts are "
        "counted (steps_analysis.py:520). Count the run's drafts, not the pending ones",
    ),
}


@pytest.mark.parametrize(
    "step",
    [pytest.param(s, marks=BOUNDARY_BUGS.get(s, ())) for s in STEPS],
    ids=[s.value for s in STEPS],
)
async def test_a_boundary_kill_leaves_the_same_final_state_as_a_clean_run(step: RunStep) -> None:
    clean, clean_run = await finished_world()
    w = World()
    kill_at_boundary(w, step)
    run = await w.start()
    with pytest.raises(AgentKilled):
        await w.run(run)
    w.store.before_commit = None
    await w.run(run)
    assert fingerprint(w.store, run.id) == fingerprint(clean.store, clean_run.id)


@pytest.mark.parametrize("name", ["plan_queries@QueryPlanner", "analyse@GapAnalyst", "verify@first-critic"])
async def test_a_midstep_kill_leaves_the_same_final_state_as_a_clean_run(name: str) -> None:
    w, clean, _, _ = await killed_and_resumed(MIDSTEP[name])
    [run], [clean_run] = w.store.runs.values(), clean.store.runs.values()
    assert fingerprint(w.store, run.id) == fingerprint(clean.store, clean_run.id)


@src_bug(
    1,
    "same defect as the boundary case: after a kill in group_by_topic the labelled clusters make the "
    "resumed run partial (steps_retrieval.py:344)",
)
async def test_a_kill_while_labelling_leaves_the_same_final_state_as_a_clean_run() -> None:
    w, clean, _, _ = await killed_and_resumed(MIDSTEP["group_by_topic@second-label"])
    [run], [clean_run] = w.store.runs.values(), clean.store.runs.values()
    assert fingerprint(w.store, run.id) == fingerprint(clean.store, clean_run.id)


# ---- killing every execution ----------------------------------------------------------------------------------------
@src_bug(
    1,
    "chained resume passes through group_by_topic with every cluster already labelled -> spurious label_fallback",
)
async def test_chained_boundary_kills_one_per_step_still_end_identical() -> None:
    clean, clean_run = await finished_world()
    w = World()
    run = await w.start()
    for step in STEPS:
        kill_at_boundary(w, step)
        with pytest.raises(AgentKilled):
            await w.run(run)
    w.store.before_commit = None
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.generation == len(STEPS) + 1
    assert call_names(w) == call_names(clean)
    assert fingerprint(w.store, run.id) == fingerprint(clean.store, clean_run.id)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(gaps=st.lists(st.integers(min_value=1, max_value=4), min_size=1, max_size=4))
def test_any_sequence_of_agent_kills_ends_done_with_every_unit_done_once(gaps: list[int]) -> None:
    """Kill the n-th, then the m-th, ... agent call of successive executions; the last execution is left alone."""

    async def scenario() -> None:
        clean, clean_run = await finished_world()
        w = World()
        run = await w.start()
        for gap in gaps:
            w.agents.kill_on_call = len(w.agents.calls) + gap
            try:
                await w.run(run)
            except AgentKilled:
                continue
            break  # finished before the kill point
        w.agents.kill_on_call = None
        await w.run(run)  # a no-op ("skipped") when it already finished
        final = w.get(run)
        assert final.status is RunStatus.DONE
        killed_calls = len(w.agents.calls) - len(clean.agents.calls)
        assert 0 <= killed_calls <= len(gaps)  # only in-flight calls are ever repeated
        assert final.spend.llm_calls == len(clean.agents.calls)  # one settled call per unit, killed ones unsettled
        assert final.spend.openalex_calls == len(w.literature.calls) == len(clean.literature.calls)
        assert row_ids(w.store, run.id) == row_ids(clean.store, clean_run.id)
        assert_user_view_clean(w.store)

    asyncio.run(scenario())
