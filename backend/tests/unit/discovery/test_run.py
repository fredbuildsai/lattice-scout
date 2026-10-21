"""AnalysisRun state machine, fencing and progress.

Covers plan M3 task 3 (run pipeline and resumability) and task 4 (progress), ARCHITECTURE section 4.2,
DECISIONS 56 (run state model) and 57 (resumability and fencing); design note M3 section 1 ("AnalysisRun").
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from tests.unit.discovery._builders import (
    at,
    cancelled_run,
    done_run,
    failed_run,
    make_run,
    running_run,
)

from scout.discovery.domain.enums import RunFailure, RunStatus, RunStep, StepStatus
from scout.discovery.domain.run import (
    STEP_ORDER,
    STEP_WEIGHTS,
    AnalysisRun,
    Degradation,
    RunCancelled,
    RunFailed,
    Superseded,
)
from scout.shared import Conflict, InvalidStateTransition

S = RunStatus
STEPS = list(RunStep)
CUMULATIVE = [5, 20, 30, 40, 50, 75, 95, 100]


# ---- constants --------------------------------------------------------------------------------------------------
def test_steps_are_ordered_and_weighted_as_designed() -> None:
    assert [s.value for s in STEP_ORDER] == [
        "plan_queries", "retrieve", "group_by_topic", "coverage", "measure_novelty", "analyse", "verify", "score_persist",
    ]  # fmt: skip
    assert [STEP_WEIGHTS[s] for s in STEP_ORDER] == [5, 15, 10, 10, 10, 25, 20, 5]
    assert sum(STEP_WEIGHTS.values()) == 100


def test_run_and_cancel_exceptions_are_distinct_domain_errors() -> None:
    assert issubclass(Superseded, Conflict) and not issubclass(RunCancelled, Conflict)
    assert RunFailed(RunFailure.BUDGET_EXHAUSTED).failure is RunFailure.BUDGET_EXHAUSTED


# ---- the transition table ---------------------------------------------------------------------------------------
BUILDERS: dict[RunStatus, Callable[[], AnalysisRun]] = {
    S.QUEUED: make_run,
    S.RUNNING: lambda: running_run(RunStep.RETRIEVE)[0],
    S.DONE: done_run,
    S.FAILED: failed_run,
    S.CANCELLED: cancelled_run,
}

ACTIONS: dict[str, Callable[[AnalysisRun], Any]] = {
    "begin_execution": lambda r: r.begin_execution(at(50)),
    "complete_step": lambda r: r.complete_step(r.step or RunStep.PLAN_QUERIES, r.generation, {}, at(50)),
    "record_progress": lambda r: r.record_progress(
        r.step or RunStep.PLAN_QUERIES, r.generation, 1, 2, at(50)
    ),
    "degrade": lambda r: r.degrade(r.step or RunStep.PLAN_QUERIES, "why", r.generation),
    "fail": lambda r: r.fail(RunFailure.INTERNAL_ERROR, r.step, at(50)),
    "request_cancel": lambda r: r.request_cancel(at(50)),
    "cancel": lambda r: r.cancel(r.generation, at(50)),
}

ALLOWED: dict[str, set[RunStatus]] = {
    "begin_execution": {S.QUEUED, S.RUNNING},
    "complete_step": {S.RUNNING},
    "record_progress": {S.RUNNING},
    "degrade": {S.RUNNING},
    "fail": {S.QUEUED, S.RUNNING},
    "request_cancel": {S.QUEUED, S.RUNNING},
    "cancel": {S.RUNNING},
}


@pytest.mark.parametrize("status", list(S))
@pytest.mark.parametrize("action", list(ACTIONS))
def test_transition_table(action: str, status: RunStatus) -> None:
    run = BUILDERS[status]()
    assert run.status is status
    before = copy.deepcopy(run)
    if status in ALLOWED[action]:
        ACTIONS[action](run)  # must not raise
        return
    with pytest.raises(InvalidStateTransition):
        ACTIONS[action](run)
    assert run == before, "a denied transition must not change anything"


# ---- begin_execution --------------------------------------------------------------------------------------------
def test_first_begin_execution_starts_the_run() -> None:
    run = make_run()
    gen = run.begin_execution(at(5))
    assert gen == run.generation == 1 and run.executions == 1
    assert run.status is S.RUNNING and run.step is RunStep.PLAN_QUERIES
    assert run.started_at == at(5) and run.finished_at is None


def test_resume_bumps_generation_and_keeps_step_and_started_at() -> None:
    run, g1 = running_run(RunStep.ANALYSE)
    started = run.started_at
    g2 = run.begin_execution(at(500))
    assert (g1, g2) == (1, 2) and run.generation == 2 and run.executions == 2
    assert run.status is S.RUNNING and run.step is RunStep.ANALYSE
    assert run.started_at == started


@pytest.mark.parametrize("n", [1, 2, 5, 17])
def test_generation_strictly_increases_with_every_execution(n: int) -> None:
    run = make_run()
    gens = [run.begin_execution(at(i)) for i in range(n)]
    assert gens == list(range(1, n + 1))
    assert run.generation == run.executions == n


# ---- complete_step ----------------------------------------------------------------------------------------------
def test_all_eight_steps_in_order_then_done() -> None:
    run = make_run()
    gen = run.begin_execution(at(1))
    assert run.percent() == 0
    for i, step in enumerate(STEPS):
        assert run.step is step and run.status is S.RUNNING
        run.complete_step(step, gen, {"n": i}, at(10 + i))
        assert run.step_state[step].summary == {"n": i}
        assert run.step_state[step].status is StepStatus.DONE
        assert run.step_state[step].finished_at == at(10 + i)
        assert run.percent() == CUMULATIVE[i]
        if i < len(STEPS) - 1:
            assert run.step is STEPS[i + 1] and run.finished_at is None
    assert run.status is S.DONE and run.finished_at == at(17) and not run.active
    assert run.percent() == 100


def test_a_degraded_step_is_recorded_as_degraded_when_it_completes() -> None:
    run, gen = running_run(RunStep.RETRIEVE)
    run.degrade(RunStep.RETRIEVE, "openalex_cap", gen)
    run.complete_step(RunStep.RETRIEVE, gen, {"works": 3}, at(60))
    assert run.step_state[RunStep.RETRIEVE].status is StepStatus.DEGRADED
    assert run.step is RunStep.GROUP_BY_TOPIC and run.status is S.RUNNING


@pytest.mark.parametrize("bad", [RunStep.PLAN_QUERIES, RunStep.COVERAGE, RunStep.SCORE_PERSIST])
def test_complete_step_needs_the_current_step(bad: RunStep) -> None:
    run, gen = running_run(RunStep.RETRIEVE)
    before = copy.deepcopy(run)
    with pytest.raises(InvalidStateTransition):
        run.complete_step(bad, gen, {}, at(70))
    assert run == before


@pytest.mark.parametrize("offset", [-1, 1, 5])
def test_complete_step_with_another_generation_is_superseded(offset: int) -> None:
    run, gen = running_run(RunStep.COVERAGE)
    before = copy.deepcopy(run)
    with pytest.raises(Superseded):
        run.complete_step(RunStep.COVERAGE, gen + offset, {}, at(70))
    assert run == before


# ---- fencing: a stale generation can never change the run -------------------------------------------------------
def test_stale_generation_cannot_change_a_resumed_run() -> None:
    run, old = running_run(RunStep.ANALYSE)
    new = run.begin_execution(at(300))
    assert new > old
    before = copy.deepcopy(run)
    stale_calls: list[Callable[[], Any]] = [
        lambda: run.complete_step(RunStep.ANALYSE, old, {}, at(301)),
        lambda: run.record_progress(RunStep.ANALYSE, old, 1, 2, at(301)),
        lambda: run.degrade(RunStep.ANALYSE, "analyse_truncated", old),
        lambda: run.cancel(old, at(301)),
    ]
    for call in stale_calls:
        with pytest.raises(Superseded):
            call()
        assert run == before
    # the new execution still works
    run.complete_step(RunStep.ANALYSE, new, {}, at(302))
    assert run.step is RunStep.VERIFY


# ---- record_progress and degrade --------------------------------------------------------------------------------
def test_record_progress_updates_counters_and_heartbeat_without_degrading() -> None:
    run, gen = running_run(RunStep.RETRIEVE)
    run.record_progress(RunStep.RETRIEVE, gen, 2, 6, at(80))
    rec = run.step_state[RunStep.RETRIEVE]
    assert (rec.done, rec.total) == (2, 6) and run.heartbeat_at == at(80)
    assert run.partial is False and run.degraded == []
    assert run.percent() == 5 + 15 * 2 // 6  # 5 + 5


def test_record_progress_with_another_generation_is_superseded() -> None:
    run, gen = running_run(RunStep.RETRIEVE)
    with pytest.raises(Superseded):
        run.record_progress(RunStep.RETRIEVE, gen + 1, 1, 2, at(80))
    assert run.step_state.get(RunStep.RETRIEVE) is None or run.step_state[RunStep.RETRIEVE].done == 0


def test_degrade_marks_partial_and_appends_in_order() -> None:
    run, gen = running_run(RunStep.GROUP_BY_TOPIC)
    assert run.partial is False
    run.degrade(RunStep.GROUP_BY_TOPIC, "label_fallback", gen)
    run.degrade(RunStep.GROUP_BY_TOPIC, "tags_skipped", gen)
    assert run.partial is True and run.status is S.RUNNING
    assert run.degraded == [
        Degradation(RunStep.GROUP_BY_TOPIC, "label_fallback"),
        Degradation(RunStep.GROUP_BY_TOPIC, "tags_skipped"),
    ]


def test_degrade_with_another_generation_is_superseded_and_leaves_no_trace() -> None:
    run, gen = running_run(RunStep.GROUP_BY_TOPIC)
    with pytest.raises(Superseded):
        run.degrade(RunStep.GROUP_BY_TOPIC, "label_fallback", gen + 1)
    assert run.partial is False and run.degraded == []


# ---- fail -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("failure", list(RunFailure))
def test_fail_from_running_records_code_step_and_time(failure: RunFailure) -> None:
    run, _ = running_run(RunStep.RETRIEVE)
    run.fail(failure, RunStep.RETRIEVE, at(90))
    assert run.status is S.FAILED and run.error_code == failure.value and run.error_step is RunStep.RETRIEVE
    assert run.finished_at == at(90) and not run.active


def test_fail_from_queued_without_a_step() -> None:
    run = make_run()
    run.fail(RunFailure.STALLED, None, at(90))
    assert run.status is S.FAILED and run.error_code == "stalled" and run.error_step is None
    assert run.finished_at == at(90)


def test_a_failed_run_is_not_done_and_never_reaches_100() -> None:
    run = failed_run()
    assert run.percent() < 100 and run.status is not S.DONE


# ---- cancel -----------------------------------------------------------------------------------------------------
def test_request_cancel_on_a_queued_run_cancels_at_once() -> None:
    run = make_run()
    run.request_cancel(at(9))
    assert run.status is S.CANCELLED and run.cancel_requested_at == at(9) and run.finished_at == at(9)
    assert not run.active


def test_request_cancel_on_a_running_run_only_records_the_request() -> None:
    run, gen = running_run(RunStep.COVERAGE)
    run.request_cancel(at(9))
    assert run.status is S.RUNNING and run.cancel_requested_at == at(9) and run.finished_at is None
    assert run.step is RunStep.COVERAGE and run.phase == "clustering"
    run.cancel(gen, at(12))
    assert run.status is S.CANCELLED and run.finished_at == at(12) and run.cancel_requested_at == at(9)


@pytest.mark.parametrize("build", [done_run, failed_run, cancelled_run])
def test_request_cancel_on_a_terminal_run_is_a_conflict(build: Callable[[], AnalysisRun]) -> None:
    run = build()
    before = copy.deepcopy(run)
    with pytest.raises(InvalidStateTransition):
        run.request_cancel(at(999))
    assert run == before


def test_a_cancelled_run_keeps_its_progress_and_cannot_move_again() -> None:
    run, gen = running_run(RunStep.VERIFY)
    pct = run.percent()
    run.cancel(gen, at(200))
    assert run.percent() == pct
    for action in ACTIONS.values():
        with pytest.raises(InvalidStateTransition):
            action(run)
    assert run.status is S.CANCELLED


# ---- phase ------------------------------------------------------------------------------------------------------
def test_phase_of_a_queued_run() -> None:
    assert make_run().phase == "queued"


@pytest.mark.parametrize(
    ("step", "phase"),
    [
        (RunStep.PLAN_QUERIES, "retrieving"), (RunStep.RETRIEVE, "retrieving"),
        (RunStep.GROUP_BY_TOPIC, "clustering"), (RunStep.COVERAGE, "clustering"),
        (RunStep.MEASURE_NOVELTY, "analysing"), (RunStep.ANALYSE, "analysing"),
        (RunStep.VERIFY, "verifying"), (RunStep.SCORE_PERSIST, "verifying"),
    ],
)  # fmt: skip
def test_phase_of_a_running_run_follows_the_step(step: RunStep, phase: str) -> None:
    run, _ = running_run(step)
    assert run.step is step and run.phase == phase


@pytest.mark.parametrize(
    ("build", "phase"), [(done_run, "done"), (failed_run, "failed"), (cancelled_run, "cancelled")]
)
def test_phase_of_a_terminal_run_is_its_status(build: Callable[[], AnalysisRun], phase: str) -> None:
    assert build().phase == phase


# ---- percent ----------------------------------------------------------------------------------------------------
def test_percent_is_zero_when_queued_and_100_when_done() -> None:
    assert make_run().percent() == 0
    assert done_run().percent() == 100


@pytest.mark.parametrize(
    ("step", "done", "total", "expected"),
    [
        (RunStep.PLAN_QUERIES, 0, 0, 0), (RunStep.RETRIEVE, 1, 3, 10), (RunStep.RETRIEVE, 3, 3, 20),
        (RunStep.ANALYSE, 2, 5, 60), (RunStep.ANALYSE, 0, 2, 50), (RunStep.VERIFY, 1, 2, 85),
    ],
)  # fmt: skip
def test_percent_adds_the_weighted_fraction_of_the_current_step(
    step: RunStep, done: int, total: int, expected: int
) -> None:
    run, gen = running_run(step)
    if total:
        run.record_progress(step, gen, done, total, at(60))
    assert run.percent() == expected


# ---- the state machine, as a random walk ------------------------------------------------------------------------
class RunMachine(RuleBasedStateMachine):
    """Random sequences of every action, including illegal and stale ones."""

    def __init__(self) -> None:
        super().__init__()
        self.run = make_run()
        self.gens: list[int] = []
        self.clock = 1
        self.last_percent = 0
        self.last_generation = 0
        self.last_step_idx = -1
        self.totals: dict[RunStep, int] = {}
        self.progress: dict[RunStep, int] = {}
        self.frozen: AnalysisRun | None = None

    def tick(self) -> Any:
        self.clock += 1
        return at(self.clock)

    def attempt(self, fn: Callable[[], Any]) -> tuple[bool, Any]:
        """Run an action; a rejected one (Conflict) must leave the run untouched."""
        before = copy.deepcopy(self.run)
        try:
            return True, fn()
        except Conflict:
            assert self.run == before, "a rejected action changed the run"
            return False, None

    def current_step(self) -> RunStep:
        return self.run.step or RunStep.PLAN_QUERIES

    @rule()
    def begin(self) -> None:
        ok, gen = self.attempt(lambda: self.run.begin_execution(self.tick()))
        if ok:
            assert gen == self.run.generation and gen > (self.gens[-1] if self.gens else 0)
            self.gens.append(gen)

    @rule()
    def complete(self) -> None:
        self.attempt(
            lambda: self.run.complete_step(self.current_step(), self.run.generation, {}, self.tick())
        )

    @rule(wrong=st.sampled_from(STEPS))
    def complete_any_step(self, wrong: RunStep) -> None:
        self.attempt(lambda: self.run.complete_step(wrong, self.run.generation, {}, self.tick()))

    @precondition(lambda self: len(self.gens) > 1)
    @rule(data=st.data())
    def stale_action(self, data: st.DataObject) -> None:
        old = data.draw(st.sampled_from(self.gens[:-1]))
        which = data.draw(st.integers(0, 3))
        step = self.current_step()
        before = copy.deepcopy(self.run)
        calls: list[Callable[[], Any]] = [
            lambda: self.run.complete_step(step, old, {}, self.tick()),
            lambda: self.run.record_progress(step, old, 1, 2, self.tick()),
            lambda: self.run.degrade(step, "stale", old),
            lambda: self.run.cancel(old, self.tick()),
        ]
        with pytest.raises(Conflict):
            calls[which]()
        assert self.run == before

    @rule(frac=st.integers(0, 10), total=st.integers(1, 10))
    def progress_step(self, frac: int, total: int) -> None:
        step = self.current_step()
        total = self.totals.setdefault(step, total)
        done = min(total, max(self.progress.get(step, 0), frac))
        ok, _ = self.attempt(
            lambda: self.run.record_progress(step, self.run.generation, done, total, self.tick())
        )
        if ok:
            self.progress[step] = done

    @rule(reason=st.text(min_size=1, max_size=8))
    def degrade(self, reason: str) -> None:
        self.attempt(lambda: self.run.degrade(self.current_step(), reason, self.run.generation))

    @rule(failure=st.sampled_from(list(RunFailure)))
    def fail(self, failure: RunFailure) -> None:
        self.attempt(lambda: self.run.fail(failure, self.run.step, self.tick()))

    @rule()
    def request_cancel(self) -> None:
        self.attempt(lambda: self.run.request_cancel(self.tick()))

    @rule()
    def cancel(self) -> None:
        self.attempt(lambda: self.run.cancel(self.run.generation, self.tick()))

    @invariant()
    def percent_never_decreases(self) -> None:
        pct = self.run.percent()
        assert 0 <= pct <= 100 and pct >= self.last_percent
        self.last_percent = pct
        if self.run.status is S.DONE:
            assert pct == 100
        if self.run.status is S.QUEUED:
            assert pct == 0

    @invariant()
    def generation_only_grows_through_begin(self) -> None:
        assert self.run.generation >= self.last_generation
        assert self.run.generation == self.run.executions == len(self.gens)
        self.last_generation = self.run.generation

    @invariant()
    def steps_only_advance(self) -> None:
        if self.run.step is not None:
            idx = STEPS.index(self.run.step)
            assert idx >= self.last_step_idx
            self.last_step_idx = idx

    @invariant()
    def terminal_states_are_final(self) -> None:
        if self.run.status in (S.DONE, S.FAILED, S.CANCELLED):
            if self.frozen is None:
                self.frozen = copy.deepcopy(self.run)
                assert self.run.finished_at is not None
            assert self.run == self.frozen
        else:
            assert self.frozen is None and self.run.finished_at is None and self.run.active


RunMachine.TestCase.settings = settings(max_examples=60, stateful_step_count=45, deadline=None)
TestRunMachine = RunMachine.TestCase
