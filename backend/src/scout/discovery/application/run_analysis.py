"""``RunAnalysis``: the ``discovery.run`` job (resumable, fenced by the generation) and the ``discovery.sweep`` job."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.discovery.application.guard import BudgetedLiterature, RunGuard
from scout.discovery.application.pipeline import PipelineConfig, StepContext
from scout.discovery.application.steps_analysis import (
    analyse,
    measure_novelty,
    score_persist,
    verify,
)
from scout.discovery.application.steps_retrieval import (
    Summary,
    coverage,
    group_by_topic,
    plan_queries,
    retrieve,
)
from scout.discovery.domain.budget import BudgetExhausted
from scout.discovery.domain.enums import RunFailure, RunStatus, RunStep
from scout.discovery.domain.field import FieldConfig
from scout.discovery.domain.run import AnalysisRun, RunCancelled, RunFailed, Superseded
from scout.discovery.ports.agents import DiscoveryAgentsPort
from scout.discovery.ports.ledger import RunLedgerPort
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.discovery.ports.sources import ProfileSource, VaultSource
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.literature import LiteraturePort
from scout.ports.llm import LLMOutputInvalid, LLMUnavailable
from scout.shared import Clock, InfrastructureError, UuidGenerator

log = structlog.get_logger()
Result = dict[str, JsonValue]
StepFn = Callable[[StepContext], Awaitable[Summary]]
STEPS: dict[RunStep, StepFn] = {
    RunStep.PLAN_QUERIES: plan_queries,
    RunStep.RETRIEVE: retrieve,
    RunStep.GROUP_BY_TOPIC: group_by_topic,
    RunStep.COVERAGE: coverage,
    RunStep.MEASURE_NOVELTY: measure_novelty,
    RunStep.ANALYSE: analyse,
    RunStep.VERIFY: verify,
    RunStep.SCORE_PERSIST: score_persist,
}
_IDS = UuidGenerator()


def failure_code(exc: BaseException) -> RunFailure:
    """Plain failure code of an exception (codes only, never its message)."""
    if isinstance(exc, LLMUnavailable | LLMOutputInvalid):
        return RunFailure.LLM_UNAVAILABLE
    if isinstance(exc, BudgetExhausted):
        return RunFailure.BUDGET_EXHAUSTED
    if isinstance(exc, InfrastructureError):
        return RunFailure.LITERATURE_UNAVAILABLE
    return RunFailure.INTERNAL_ERROR


class RunAnalysis:
    """Drive a run through its steps; a retry resumes at ``run.step`` with a new generation."""

    def __init__(
        self,
        uow: Callable[[], DiscoveryUnitOfWork],
        ledger: RunLedgerPort,
        literature: LiteraturePort,
        agents: DiscoveryAgentsPort,
        profiles: ProfileSource,
        vault: VaultSource,
        field: FieldConfig,
        cfg: PipelineConfig,
        clock: Clock,
        on_done: Callable[[UUID, UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._on_done = on_done
        self._uow, self._ledger, self._literature, self._agents = uow, ledger, literature, agents
        self._profiles, self._vault, self._field, self._cfg, self._clock = profiles, vault, field, cfg, clock

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> Result:
        """One execution of the job. Results carry codes, ids and counts only."""
        run_id = UUID(str(payload["run_id"]))
        async with self._uow() as uow:
            run = await uow.runs.get_for_update(run_id)
            if run is None:
                return {"skipped": "gone"}
            if not run.active:
                return {"skipped": run.status.value}
            gen = run.begin_execution(self._clock.now())
            owner, session = run.owner_id, run.session_id
            if run.cancel_requested_at is not None:
                run.cancel(gen, self._clock.now())
            await uow.runs.save(run)
            await uow.commit()
            budget, spend = run.budget, run.spend
        if run.status is RunStatus.CANCELLED:
            return {"cancelled": True}
        profile = await self._profiles.run_profile(owner)
        if profile is None:
            await self._fail(run_id, gen, RunFailure.PROFILE_MISSING, run.step)
            return {"failed": RunFailure.PROFILE_MISSING.value}
        guard = RunGuard(self._ledger, run_id, gen, budget, spend)
        ctx = StepContext(
            run_id=run_id, owner_id=owner, session_id=session, generation=gen, uow=self._uow, guard=guard,
            literature=BudgetedLiterature(self._literature, guard), agents=self._agents, vault=self._vault,
            profile=profile, field=self._field, cfg=self._cfg, clock=self._clock, last_attempt=last_attempt,
        )  # fmt: skip
        step: RunStep | None = run.step
        try:
            while True:
                current = await ctx.read()
                step = current.step
                if current.status is not RunStatus.RUNNING or step is None:
                    break
                if current.cancel_requested_at is not None:
                    raise RunCancelled("The run was cancelled")
                summary = await STEPS[step](ctx)

                async def done(
                    uow: DiscoveryUnitOfWork, r: AnalysisRun, s: RunStep = step, summary: Summary = summary
                ) -> None:
                    r.complete_step(s, gen, summary, self._clock.now())

                await ctx.tx(done)
        except Superseded:
            return {"skipped": "superseded"}
        except RunCancelled:
            await self._cancel(run_id, gen)
            return {"cancelled": True}
        except RunFailed as exc:
            await self._fail(run_id, gen, exc.failure, step)
            return {"failed": exc.failure.value}
        except BudgetExhausted:
            await self._fail(run_id, gen, RunFailure.BUDGET_EXHAUSTED, step)
            return {"failed": RunFailure.BUDGET_EXHAUSTED.value}
        except Exception as exc:
            if last_attempt:
                await self._fail(run_id, gen, failure_code(exc), step)
            raise
        final = await ctx.read()
        if final.status is RunStatus.DONE and self._on_done is not None:
            await self._on_done(owner, run_id)  # best effort: the callback never raises
        return {"status": final.status.value, "percent": final.percent(), "partial": final.partial}

    async def _fail(self, run_id: UUID, gen: int, failure: RunFailure, step: RunStep | None) -> None:
        async with self._uow() as uow:
            run = await uow.runs.get_for_update(run_id)
            if run is not None and run.generation == gen and run.active:
                run.fail(failure, step, self._clock.now())
                await uow.runs.save(run)
                await uow.commit()
        log.warning("discovery.run_failed", run_id=str(run_id), reason=failure.value)

    async def _cancel(self, run_id: UUID, gen: int) -> None:
        async with self._uow() as uow:
            run = await uow.runs.get_for_update(run_id)
            if run is not None and run.generation == gen and run.status is RunStatus.RUNNING:
                run.cancel(gen, self._clock.now())
                await uow.runs.save(run)
                await uow.commit()


class SweepRuns:
    """``discovery.sweep``: re-enqueue stuck runs, fail the ones out of executions, reschedule itself."""

    KIND = "discovery.sweep"

    def __init__(
        self,
        uow: Callable[[], DiscoveryUnitOfWork],
        jobs: JobQueuePort,
        clock: Clock,
        *,
        interval_minutes: int = 10,
        queued_minutes: int = 5,
        stall_minutes: int = 20,
        max_executions: int = 8,
        limit: int = 100,
    ) -> None:
        self._uow, self._jobs, self._clock = uow, jobs, clock
        self._interval, self._queued, self._stall = interval_minutes, queued_minutes, stall_minutes
        self._max_exec, self._limit = max_executions, limit

    async def ensure_scheduled(self) -> None:
        """Queue the next sweep in the current slot (deduped, so it is idempotent)."""
        now = self._clock.now()
        slot = int(now.timestamp() // (self._interval * 60)) + 1
        await self._jobs.enqueue(
            JobSpec(
                kind=self.KIND,
                payload={"feature": self.KIND},
                run_at=now + timedelta(minutes=self._interval),
                dedupe_key=f"slot:{slot}",
                max_attempts=3,
            )  # fmt: skip
        )

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> Result:
        """One sweep. The next one is scheduled first so a failure below never ends the chain."""
        await self.ensure_scheduled()
        now = self._clock.now()
        async with self._uow() as uow:
            stale = await uow.runs.list_stale(
                now - timedelta(minutes=self._queued),
                now - timedelta(minutes=self._stall),
                self._max_exec,
                self._limit,
            )
            owners: dict[UUID, str] = {}
            for rid in (*stale.queued, *stale.stalled):
                run = await uow.runs.get_for_update(rid)
                if run is not None:
                    owners[rid] = str(run.owner_id)
            failed = 0
            for rid in stale.exhausted:
                run = await uow.runs.get_for_update(rid)
                if run is not None and run.active:
                    run.fail(RunFailure.STALLED, run.step, now)
                    await uow.runs.save(run)
                    failed += 1
            await uow.commit()
        for rid, owner in owners.items():
            await self._jobs.enqueue(
                JobSpec(
                    kind="discovery.run",
                    payload={"account_id": owner, "feature": "discovery.run", "run_id": str(rid)},
                    dedupe_key=str(rid),
                    max_attempts=5,
                )  # fmt: skip
            )
        counts: Result = {"queued": len(stale.queued), "stalled": len(stale.stalled), "failed": failed}
        log.info("discovery.sweep", **counts)
        return counts
