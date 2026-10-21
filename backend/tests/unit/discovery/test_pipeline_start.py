"""Starting, queueing, cancelling and sweeping runs (plan M3 task 2; design M3 sections 2.1 and 2.2, DECISIONS 56-57).

* ``StartAnalysisRun``: document validation, profile requirement, one active run per owner, daily run and money
  caps, the effective budget ``min(run budget, remaining daily budget)`` and the deduplicated ``discovery.run`` job.
* Cancellation: at once while queued; while running at the next step boundary or the next ledger charge. A
  cancelled run keeps its rows and has shown nothing.
* ``SweepRuns`` (``discovery.sweep``): re-enqueues stuck runs, fails the ones out of executions, reschedules itself.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from structlog.testing import capture_logs
from tests.fakes.discovery import AgentKilled
from tests.unit.discovery._world import (
    DOC_ID,
    NOW,
    OWNER,
    STEP_CALLS,
    STEPS,
    World,
    after_step,
    assert_user_view_clean,
    call_names,
)

from scout.discovery.application.run_analysis import SweepRuns
from scout.discovery.application.start import (
    DailyBudgetReached,
    DocumentNotReady,
    ProfileRequired,
    RunLimitReached,
    StartConfig,
)
from scout.discovery.domain.budget import RunBudget, RunSpend
from scout.discovery.domain.enums import OppStatus, RunFailure, RunStatus, RunStep
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.ports.repos import RunInProgress
from scout.discovery.ports.sources import DocMaterial
from scout.ports.jobs import Job, JobSpec, JobStatus
from scout.shared import AccountId, NotFound, ValidationFailed

OTHER = AccountId(UUID(int=12))


def doc(i: int, *, retention: str = "discard") -> DocMaterial:
    return DocMaterial(UUID(int=700 + i), f"d{i}", f"file{i}.pdf", retention, True)  # type: ignore[arg-type]


def add_docs(w: World, n: int, *, ready: bool = True, owner: AccountId = OWNER) -> tuple[UUID, ...]:
    for i in range(1, n + 1):
        w.vault.add(owner, doc(i), ready=ready)
    return tuple(UUID(int=700 + i) for i in range(1, n + 1))


def seed_run(
    w: World,
    n: int,
    *,
    status: str = "queued",
    age_minutes: float = 0,
    heartbeat_minutes: float | None = None,
    executions: int = 0,
    cost: str = "0",
    owner: AccountId | None = None,
) -> AnalysisRun:
    """Insert a run straight into the store (the sweeper and the limits only look at the stored rows)."""
    created = NOW - timedelta(minutes=age_minutes)
    run = AnalysisRun(
        id=UUID(int=9000 + n), owner_id=owner or AccountId(UUID(int=100 + n)), created_at=created,
        budget=RunBudget(usd=Decimal(1)), spend=RunSpend(cost_usd=Decimal(cost)),
    )  # fmt: skip
    if status in ("running", "done", "failed"):
        run.begin_execution(created)
        run.executions = executions or 1
        run.heartbeat_at = NOW - timedelta(
            minutes=heartbeat_minutes if heartbeat_minutes is not None else age_minutes
        )
    if status == "done":
        run.status = RunStatus.DONE
    if status == "failed":
        run.fail(RunFailure.INTERNAL_ERROR, None, created)
    if status == "cancelled":
        run.request_cancel(created)
    w.store.runs[run.id] = run
    return run


def jobs_of(w: World, kind: str) -> list[Job]:
    return [j for j in w.jobs.jobs.values() if j.kind == kind]


# ---- documents --------------------------------------------------------------------------------------------------------
async def test_a_run_without_documents_is_allowed_and_queued() -> None:
    w = World()
    run = await w.start(())
    assert run.status is RunStatus.QUEUED and run.inputs.documents == () and run.generation == 0
    assert w.store.runs[run.id].owner_id == OWNER


async def test_documents_are_labelled_d1_to_dn_in_request_order_with_their_retention() -> None:
    w = World()
    w.vault.add(OWNER, doc(1, retention="keep"))
    w.vault.add(OWNER, doc(2))
    w.vault.add(OWNER, doc(3))
    ids = (UUID(int=703), UUID(int=701), UUID(int=702))
    run = await w.start(ids)
    assert [(d.label, d.document_id, d.retention) for d in run.inputs.documents] == [
        ("d1", ids[0], "discard"), ("d2", ids[1], "keep"), ("d3", ids[2], "discard"),
    ]  # fmt: skip
    assert w.store.runs[run.id].inputs.documents == run.inputs.documents


async def test_ten_documents_are_accepted_and_eleven_refused() -> None:
    w = World()
    ids = add_docs(w, 11)
    assert (await w.start(ids[:10])).status is RunStatus.QUEUED
    w2 = World()
    ids2 = add_docs(w2, 11)
    with pytest.raises(ValidationFailed) as exc:
        await w2.start(ids2)
    assert exc.value.extra == {"max": 10}
    assert not w2.store.runs and not w2.jobs.jobs


async def test_the_same_document_twice_is_refused() -> None:
    w = World()
    ids = add_docs(w, 2)
    with pytest.raises(ValidationFailed):
        await w.start((ids[0], ids[1], ids[0]))
    assert not w.store.runs


async def test_another_accounts_document_is_not_found() -> None:
    w = World()
    foreign = add_docs(w, 1, owner=OTHER)
    with pytest.raises(NotFound):
        await w.start(foreign)
    assert not w.store.runs


async def test_a_document_that_does_not_exist_is_not_found() -> None:
    w = World()
    with pytest.raises(NotFound):
        await w.start((UUID(int=12345),))


async def test_a_document_that_is_not_ready_is_refused_with_its_id() -> None:
    w = World()
    w.vault.add(OWNER, doc(1), ready=False)
    with pytest.raises(DocumentNotReady) as exc:
        await w.start((UUID(int=701),))
    assert exc.value.extra == {"document_id": str(UUID(int=701))}
    assert exc.value.http_status == 422 and exc.value.code == "document_not_ready"
    assert not w.store.runs and not w.jobs.jobs


async def test_a_foreign_document_is_reported_as_not_found_even_next_to_a_not_ready_one() -> None:
    w = World()
    w.vault.add(OWNER, doc(1), ready=False)
    w.vault.add(OTHER, doc(2))
    with pytest.raises(NotFound):
        await w.start(
            (UUID(int=701), UUID(int=702))
        )  # existence is checked before readiness: no probing of others


# ---- profile ----------------------------------------------------------------------------------------------------------
async def test_without_a_profile_the_run_needs_one() -> None:
    w = World()
    del w.profiles.profiles[OWNER]
    with pytest.raises(ProfileRequired) as exc:
        await w.start()
    assert exc.value.code == "profile_required" and exc.value.http_status == 409
    assert not w.store.runs


async def test_a_profile_whose_import_is_unfinished_is_not_enough() -> None:
    w = World()
    w.profiles.profiles[OWNER] = w.profiles.profiles[OWNER].__class__(
        researcher_id=UUID(int=77), topics=(), works=(), import_done=False
    )
    with pytest.raises(ProfileRequired):
        await w.start()


# ---- one active run, daily caps ---------------------------------------------------------------------------------------
async def test_an_active_run_blocks_a_second_one_and_names_it() -> None:
    w = World()
    first = await w.start()
    with pytest.raises(RunInProgress) as exc:
        await w.start()
    assert exc.value.extra == {"run_id": str(first.id)} and exc.value.http_status == 409
    assert len(w.store.runs) == 1 and len(jobs_of(w, "discovery.run")) == 1


async def test_a_running_run_blocks_too_and_a_finished_one_does_not() -> None:
    w = World()
    first = await w.start()
    w.store.runs[first.id].begin_execution(NOW)
    with pytest.raises(RunInProgress):
        await w.start()
    await w.run(first)  # the job finishes the run
    assert w.get(first).status is RunStatus.DONE
    second = await w.start()
    assert second.id != first.id


async def test_another_accounts_active_run_does_not_block() -> None:
    w = World()
    seed_run(w, 1, status="running", owner=OTHER)
    assert (await w.start()).status is RunStatus.QUEUED


async def test_the_sixth_run_within_24_hours_hits_the_daily_run_limit() -> None:
    w = World(start_cfg=StartConfig(max_runs_per_day=5))
    for _ in range(5):
        run = await w.start()
        w.store.runs[run.id].request_cancel(w.clock.now())  # free the single active slot
    with pytest.raises(RunLimitReached) as exc:
        await w.start()
    assert exc.value.code == "run_limit_reached" and exc.value.http_status == 429
    assert len(w.store.runs) == 5


async def test_runs_older_than_24_hours_do_not_count_against_the_limit() -> None:
    w = World(start_cfg=StartConfig(max_runs_per_day=2))
    for _ in range(2):
        run = await w.start()
        w.store.runs[run.id].request_cancel(w.clock.now())
    with pytest.raises(RunLimitReached):
        await w.start()
    w.clock.advance(timedelta(hours=24, minutes=1))
    assert (await w.start()).status is RunStatus.QUEUED


async def test_a_nearly_spent_daily_budget_refuses_the_run() -> None:
    w = World()
    seed_run(w, 1, status="done", owner=OWNER, age_minutes=60, cost="4.90")  # 0.10 USD left of 5.00
    with pytest.raises(DailyBudgetReached) as exc:
        await w.start()
    assert exc.value.code == "daily_budget_reached" and exc.value.http_status == 429
    assert len(w.store.runs) == 1 and not w.jobs.jobs


async def test_the_smallest_useful_remaining_budget_is_exactly_one_reasoning_and_three_fast_reserves() -> (
    None
):
    w = World()
    seed_run(w, 1, status="done", owner=OWNER, age_minutes=60, cost="4.64")  # 0.36 left = 0.30 + 3 x 0.02
    run = await w.start()
    assert run.budget.usd == Decimal("0.36")
    w2 = World()
    seed_run(w2, 1, status="done", owner=OWNER, age_minutes=60, cost="4.65")  # 0.35 left: one cent short
    with pytest.raises(DailyBudgetReached):
        await w2.start()


async def test_spend_older_than_24_hours_is_forgotten() -> None:
    w = World()
    seed_run(w, 1, status="done", owner=OWNER, age_minutes=25 * 60, cost="4.90")
    assert (await w.start()).budget.usd == Decimal("1.50")


@pytest.mark.parametrize(
    ("spent", "expected"),
    [("0", "1.50"), ("3.00", "1.50"), ("3.50", "1.50"), ("4.00", "1.00"), ("4.50", "0.50")],
)
async def test_the_effective_budget_is_the_smaller_of_the_run_budget_and_what_is_left_today(
    spent: str, expected: str
) -> None:
    w = World()
    seed_run(w, 1, status="done", owner=OWNER, age_minutes=60, cost=spent)
    run = await w.start()
    assert run.budget.usd == Decimal(expected)
    assert w.store.runs[run.id].budget.usd == Decimal(expected)


async def test_the_budget_carries_the_configured_openalex_cap_and_corpus_cap() -> None:
    w = World(start_cfg=StartConfig(openalex_calls=5, max_corpus=40))
    run = await w.start()
    assert run.budget.openalex_calls == 5 and run.budget.max_corpus == 40


# ---- the job ----------------------------------------------------------------------------------------------------------
async def test_starting_enqueues_one_deduplicated_run_job_with_ids_only() -> None:
    w = World()
    session = UUID(int=31)
    run = await w.starter.start(OWNER, (DOC_ID,), session)
    [job] = jobs_of(w, "discovery.run")
    assert job.status is JobStatus.QUEUED and job.max_attempts == 5
    assert job.payload == {
        "account_id": str(OWNER), "session_id": str(session), "feature": "discovery.run", "run_id": str(run.id),
    }  # fmt: skip
    assert w.jobs.dedupe[job.id] == ("discovery.run", str(run.id))
    again = await w.jobs.enqueue(JobSpec(kind="discovery.run", payload=job.payload, dedupe_key=str(run.id)))
    assert again == job.id and len(jobs_of(w, "discovery.run")) == 1


async def test_a_failing_queue_is_logged_and_leaves_a_queued_run_for_the_sweeper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = World()

    async def down(spec: JobSpec) -> UUID:
        raise RuntimeError("queue down")

    monkeypatch.setattr(w.jobs, "enqueue", down)
    with capture_logs() as logs:
        run = await w.start()
    assert run.status is RunStatus.QUEUED and w.store.runs[run.id].status is RunStatus.QUEUED
    assert any(e["event"] == "discovery.enqueue_failed" and e["run_id"] == str(run.id) for e in logs)
    assert "queue down" not in str(logs)  # the error text stays out of the log line


async def test_the_job_payload_runs_the_pipeline() -> None:
    w = World()
    run = await w.start()
    job = await w.jobs.claim("worker-1", ["discovery.run"])
    assert job is not None
    result = await w.runner.handle(job.payload, last_attempt=False)
    assert result["status"] == "done" and w.get(run).status is RunStatus.DONE


async def test_an_unknown_run_is_skipped_not_failed() -> None:
    w = World()
    assert await w.runner.handle({"run_id": str(UUID(int=404))}, last_attempt=False) == {"skipped": "gone"}


# ---- cancel -----------------------------------------------------------------------------------------------------------
async def test_cancelling_a_queued_run_cancels_it_at_once_and_the_job_does_nothing() -> None:
    w = World()
    run = await w.start()
    w.store.runs[run.id].request_cancel(w.clock.now())
    stored = w.get(run)
    assert stored.status is RunStatus.CANCELLED and stored.finished_at is not None
    assert await w.run(run) == {"skipped": "cancelled"}
    assert w.agents.calls == [] and w.literature.calls == [] and not w.opps(run)
    assert (await w.start()).status is RunStatus.QUEUED  # the active slot is free again


async def test_a_cancel_flag_on_a_queued_run_is_honoured_when_the_job_starts() -> None:
    w = World()
    run = await w.start()
    w.store.runs[run.id].cancel_requested_at = NOW
    assert await w.run(run) == {"cancelled": True}
    stored = w.get(run)
    assert stored.status is RunStatus.CANCELLED and w.agents.calls == []


@pytest.mark.parametrize("step", STEPS[:-1], ids=[s.value for s in STEPS[:-1]])
async def test_cancelling_a_running_run_stops_it_at_the_next_step_boundary(step: RunStep) -> None:
    w = World()
    run = await w.start()
    after_step(w, step, lambda stored: stored.request_cancel(w.clock.now()))
    assert await w.run(run) == {"cancelled": True}
    stored = w.get(run)
    assert stored.status is RunStatus.CANCELLED and stored.finished_at is not None
    assert stored.step is STEPS[STEPS.index(step) + 1]  # nothing of the next step ran
    allowed = set().union(*(STEP_CALLS[s] for s in STEPS[: STEPS.index(step) + 1]))
    assert set(call_names(w)) <= allowed
    assert not w.shown(run) and all(o.status is OppStatus.DRAFT for o in w.opps(run))
    assert stored.percent() < 100
    assert_user_view_clean(w.store)


# charge number -> step it happens in, for a clean run: 1 plan, 2-3 search, 4-6 label, 7 tag, 8-9 count, 10 analyse, 11-12 critic
CHARGE_STEP = [
    RunStep.PLAN_QUERIES, RunStep.RETRIEVE, RunStep.RETRIEVE, RunStep.GROUP_BY_TOPIC, RunStep.GROUP_BY_TOPIC,
    RunStep.GROUP_BY_TOPIC, RunStep.COVERAGE, RunStep.MEASURE_NOVELTY, RunStep.MEASURE_NOVELTY, RunStep.ANALYSE,
    RunStep.VERIFY, RunStep.VERIFY,
]  # fmt: skip


@pytest.mark.parametrize("n", range(1, 13))
async def test_cancelling_a_running_run_is_noticed_at_the_next_charge(n: int) -> None:
    w = World()
    run = await w.start()
    w.ledger.on_charge = lambda k: w.store.runs[run.id].request_cancel(w.clock.now()) if k == n else None
    assert await w.run(run) == {"cancelled": True}
    stored = w.get(run)
    step = CHARGE_STEP[n - 1]
    assert stored.status is RunStatus.CANCELLED and stored.step is step
    allowed = set().union(*(STEP_CALLS[s] for s in STEPS[: STEPS.index(step) + 1]))
    assert set(call_names(w)) <= allowed  # not one call of a later step
    assert not w.shown(run), "opportunities only become shown in score_persist"
    assert stored.cancel_requested_at is not None
    assert_user_view_clean(w.store)


async def test_a_cancelled_run_keeps_its_rows() -> None:
    w = World()
    run = await w.start()
    after_step(w, RunStep.ANALYSE, lambda stored: stored.request_cancel(w.clock.now()))
    await w.run(run)
    assert w.get(run).status is RunStatus.CANCELLED
    assert len(w.opps(run)) == 2 and all(o.claims for o in w.opps(run))  # the drafts stay, unchecked
    assert w.store.corpus[run.id] and w.store.clusters[run.id] and w.store.probes[run.id]


# ---- sweeper ----------------------------------------------------------------------------------------------------------
async def test_a_queued_run_older_than_five_minutes_gets_its_job_back() -> None:
    w = World()
    old = seed_run(w, 1, age_minutes=5.02)
    young = seed_run(w, 2, age_minutes=4.98)
    result = await w.sweeper.handle({}, last_attempt=False)
    assert result == {"queued": 1, "stalled": 0, "failed": 0}
    [job] = jobs_of(w, "discovery.run")
    assert job.status is JobStatus.QUEUED and job.max_attempts == 5
    assert job.payload == {"account_id": str(old.owner_id), "feature": "discovery.run", "run_id": str(old.id)}
    assert w.jobs.dedupe[job.id] == ("discovery.run", str(old.id))
    assert str(young.id) not in {j.payload["run_id"] for j in jobs_of(w, "discovery.run")}


async def test_a_running_run_with_a_stale_heartbeat_is_re_enqueued() -> None:
    w = World()
    stalled = seed_run(w, 1, status="running", age_minutes=60, heartbeat_minutes=21)
    seed_run(w, 2, status="running", age_minutes=60, heartbeat_minutes=19)  # still beating
    result = await w.sweeper.handle({}, last_attempt=False)
    assert result["stalled"] == 1 and result["failed"] == 0
    assert [j.payload["run_id"] for j in jobs_of(w, "discovery.run")] == [str(stalled.id)]
    assert w.get(stalled).status is RunStatus.RUNNING


async def test_a_run_that_used_up_its_executions_is_failed_as_stalled() -> None:
    w = World()
    spent = seed_run(w, 1, status="running", age_minutes=90, heartbeat_minutes=30, executions=8)
    seed_run(w, 2, status="running", age_minutes=90, heartbeat_minutes=30, executions=7)
    seed_run(
        w, 3, status="running", age_minutes=90, heartbeat_minutes=2, executions=8
    )  # alive: leave it alone
    result = await w.sweeper.handle({}, last_attempt=False)
    assert result == {"queued": 0, "stalled": 1, "failed": 1}
    failed = w.get(spent)
    assert failed.status is RunStatus.FAILED and failed.error_code == RunFailure.STALLED.value
    assert failed.error_step is failed.step and failed.finished_at is not None
    assert w.store.runs[UUID(int=9002)].status is RunStatus.RUNNING
    assert w.store.runs[UUID(int=9003)].status is RunStatus.RUNNING
    assert [j.payload["run_id"] for j in jobs_of(w, "discovery.run")] == [str(UUID(int=9002))]


async def test_finished_runs_are_never_touched() -> None:
    w = World()
    for n, status in enumerate(("done", "failed", "cancelled"), start=1):
        seed_run(w, n, status=status, age_minutes=600, heartbeat_minutes=600, executions=9)
    assert await w.sweeper.handle({}, last_attempt=False) == {"queued": 0, "stalled": 0, "failed": 0}
    assert not jobs_of(w, "discovery.run")


async def test_a_run_whose_job_is_still_active_gets_no_second_job() -> None:
    w = World()
    run = await w.start()
    w.clock.advance(timedelta(minutes=6))
    await w.sweeper.handle({}, last_attempt=False)
    assert len(jobs_of(w, "discovery.run")) == 1
    assert next(iter(jobs_of(w, "discovery.run"))).payload["run_id"] == str(run.id)


async def test_the_sweep_reschedules_itself_in_the_next_slot_and_is_idempotent() -> None:
    w = World()
    await w.sweeper.handle({}, last_attempt=False)
    [first] = jobs_of(w, "discovery.sweep")
    assert first.status is JobStatus.QUEUED
    assert first.run_at == NOW + timedelta(minutes=10)
    assert first.payload == {"feature": "discovery.sweep"}
    await w.sweeper.handle({}, last_attempt=False)  # same slot: deduplicated
    await w.sweeper.ensure_scheduled()
    assert len(jobs_of(w, "discovery.sweep")) == 1
    w.clock.advance(timedelta(minutes=10))
    await w.sweeper.handle({}, last_attempt=False)  # next slot: a new job
    assert len(jobs_of(w, "discovery.sweep")) == 2


async def test_sweeping_twice_changes_nothing_the_second_time() -> None:
    w = World()
    seed_run(w, 1, age_minutes=30)
    seed_run(w, 2, status="running", age_minutes=90, heartbeat_minutes=30, executions=8)
    await w.sweeper.handle({}, last_attempt=False)
    states = {rid: (r.status, r.error_code, r.generation) for rid, r in w.store.runs.items()}
    jobs = sorted(str(j.payload) for j in w.jobs.jobs.values())
    second = await w.sweeper.handle({}, last_attempt=False)
    assert second == {
        "queued": 1,
        "stalled": 0,
        "failed": 0,
    }  # the queued run is still queued, its job still active
    assert {rid: (r.status, r.error_code, r.generation) for rid, r in w.store.runs.items()} == states
    assert sorted(str(j.payload) for j in w.jobs.jobs.values()) == jobs


async def test_a_sweep_handles_at_most_its_limit_of_runs() -> None:
    w = World()
    for n in range(1, 6):
        seed_run(w, n, age_minutes=30)
    sweeper = SweepRuns(w.uow, w.jobs, w.clock, limit=2)
    assert (await sweeper.handle({}, last_attempt=False))["queued"] == 2
    assert len(jobs_of(w, "discovery.run")) == 2


async def test_a_failing_sweep_has_already_scheduled_the_next_one() -> None:
    w = World()

    def broken() -> object:
        raise RuntimeError("database down")

    sweeper = SweepRuns(broken, w.jobs, w.clock)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        await sweeper.handle({}, last_attempt=False)
    assert len(jobs_of(w, "discovery.sweep")) == 1


async def test_the_sweeper_recovers_a_run_whose_enqueue_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    w = World()
    real = w.jobs.enqueue

    async def down_for_runs(spec: JobSpec) -> UUID:
        if spec.kind == "discovery.run":
            raise RuntimeError("queue down")
        return await real(spec)

    monkeypatch.setattr(w.jobs, "enqueue", down_for_runs)
    run = await w.start()
    monkeypatch.setattr(w.jobs, "enqueue", real)
    assert not jobs_of(w, "discovery.run")
    w.clock.advance(timedelta(minutes=6))
    await w.sweeper.handle({}, last_attempt=False)
    job = await w.jobs.claim("worker-1", ["discovery.run"])
    assert job is not None
    await w.runner.handle(job.payload, last_attempt=False)
    assert w.get(run).status is RunStatus.DONE


async def test_the_sweeper_brings_back_a_killed_run_whose_job_is_dead() -> None:
    w = World()
    run = await w.start()
    job = await w.jobs.claim("worker-1", ["discovery.run"])
    assert job is not None
    w.agents.kill_on_call = 7  # first Critic
    with pytest.raises(AgentKilled):
        await w.runner.handle(job.payload, last_attempt=False)
    w.agents.kill_on_call = None
    await w.jobs.fail(job.id, "worker lost", None)  # attempts exhausted: the queue gave up
    assert w.jobs.jobs[job.id].status is JobStatus.DEAD
    w.clock.advance(timedelta(minutes=21))
    assert (await w.sweeper.handle({}, last_attempt=False))["stalled"] == 1
    again = await w.jobs.claim("worker-2", ["discovery.run"])
    assert again is not None and again.id != job.id
    await w.runner.handle(again.payload, last_attempt=False)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.executions == 2
    assert w.agents.count("QueryPlanner") == 1 and w.agents.count("GapAnalyst") == 1
