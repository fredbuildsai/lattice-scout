"""``AnalysisRun``: the aggregate that drives one pipeline execution (state machine, fencing, progress)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from scout.discovery.domain.budget import RunBudget, RunSpend
from scout.discovery.domain.enums import RunFailure, RunStatus, RunStep, StepStatus
from scout.shared import AccountId, AggregateRoot, Conflict, DomainError, InvalidStateTransition

PUBLISHED_ONLY = "published_only"
STEP_ORDER: tuple[RunStep, ...] = tuple(RunStep)
STEP_WEIGHTS: dict[RunStep, int] = {
    RunStep.PLAN_QUERIES: 5, RunStep.RETRIEVE: 15, RunStep.GROUP_BY_TOPIC: 10, RunStep.COVERAGE: 10,
    RunStep.MEASURE_NOVELTY: 10, RunStep.ANALYSE: 25, RunStep.VERIFY: 20, RunStep.SCORE_PERSIST: 5,
}  # fmt: skip


class Superseded(Conflict):
    """A newer execution owns the run (the generation changed); the caller must stop without writing."""

    code = "superseded"
    title = "Run superseded by a newer execution"


class RunCancelled(DomainError):
    """The owner cancelled the run."""

    code = "run_cancelled"
    http_status = 409
    title = "Run cancelled"


class RunFailed(DomainError):
    """A permanent failure: the job does not retry."""

    code = "run_failed"
    http_status = 500
    title = "Run failed"

    def __init__(self, failure: RunFailure) -> None:
        super().__init__(failure.value, extra={"reason": failure.value})
        self.failure = failure


_PHASES: dict[RunStep, str] = {
    RunStep.PLAN_QUERIES: "retrieving", RunStep.RETRIEVE: "retrieving",
    RunStep.GROUP_BY_TOPIC: "clustering", RunStep.COVERAGE: "clustering",
    RunStep.MEASURE_NOVELTY: "analysing", RunStep.ANALYSE: "analysing",
    RunStep.VERIFY: "verifying", RunStep.SCORE_PERSIST: "verifying",
}  # fmt: skip


@dataclass
class StepRecord:
    """Progress and small outputs of one step. ``data`` never holds vault text."""

    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    done: int = 0
    total: int = 0
    summary: dict[str, int | str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Degradation:
    """A step finished with less than full output (budget, fallback, truncation)."""

    step: RunStep
    reason: str


@dataclass(frozen=True)
class TopicRef:
    """A profile topic used by the run."""

    id: str
    name: str | None
    subfield: str | None
    weight: float


@dataclass(frozen=True)
class RunDocument:
    """A vault document attached to the run; ``label`` (``d1``) is what the agents see."""

    label: str
    document_id: UUID
    retention: str


@dataclass(frozen=True)
class RunInputs:
    """References only: no vault text."""

    researcher_id: UUID | None = None
    topics: tuple[TopicRef, ...] = ()
    documents: tuple[RunDocument, ...] = ()
    insight_refs: tuple[str, ...] = ()
    year_from: int | None = None
    novelty_from: int | None = None
    basis: str = (
        "full"  # "published_only": vault material was left out because no endpoint is authorized (ADR 016)
    )


@dataclass
class AnalysisRun(AggregateRoot):
    """One run of the gap-analysis pipeline for one owner."""

    id: UUID
    owner_id: AccountId
    created_at: datetime
    budget: RunBudget
    session_id: UUID | None = None
    status: RunStatus = RunStatus.QUEUED
    step: RunStep | None = None
    step_state: dict[RunStep, StepRecord] = field(default_factory=dict)
    inputs: RunInputs = field(default_factory=RunInputs)
    spend: RunSpend = field(default_factory=RunSpend)
    generation: int = 0
    executions: int = 0
    partial: bool = False
    degraded: list[Degradation] = field(default_factory=list)
    error_code: str | None = None
    error_step: RunStep | None = None
    cancel_requested_at: datetime | None = None
    prompt_versions: dict[str, str] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    updated_at: datetime | None = None

    # ---- guards ------------------------------------------------------------------------------------------------
    def _fence(self, generation: int) -> None:
        if generation != self.generation:
            raise Superseded("generation_changed", extra={"expected": self.generation, "got": generation})

    def _need(self, *allowed: RunStatus) -> None:
        if self.status not in allowed:
            raise InvalidStateTransition(f"run_{self.status.value}", extra={"status": self.status.value})

    def _record(self, step: RunStep) -> StepRecord:
        return self.step_state.setdefault(step, StepRecord())

    def _finish(self, status: RunStatus, now: datetime) -> None:
        self.status, self.finished_at, self.updated_at = status, now, now

    # ---- transitions -------------------------------------------------------------------------------------------
    def begin_execution(self, now: datetime) -> int:
        """``queued|running`` -> ``running``; bumps the generation and returns it (the fencing token)."""
        self._need(RunStatus.QUEUED, RunStatus.RUNNING)
        if self.status is RunStatus.QUEUED:
            self.status, self.step, self.started_at = RunStatus.RUNNING, STEP_ORDER[0], now
        self.generation += 1
        self.executions += 1
        self.heartbeat_at = self.updated_at = now
        rec = self._record(self.step or STEP_ORDER[0])
        if rec.status is StepStatus.PENDING:
            rec.status, rec.started_at = StepStatus.RUNNING, now
        return self.generation

    def complete_step(
        self, step: RunStep, generation: int, summary: dict[str, int | str], now: datetime
    ) -> None:
        """Finish ``step`` and move to the next one; after the last step the run is ``done``.

        Raises:
            Superseded: ``generation`` is not the current one.
            InvalidStateTransition: the run is not running, or ``step`` is not the current step.
        """
        self._fence(generation)
        self._need(RunStatus.RUNNING)
        if step is not self.step:
            raise InvalidStateTransition("not_current_step", extra={"step": step.value})
        rec = self._record(step)
        degraded = any(d.step is step for d in self.degraded)
        rec.status = StepStatus.DEGRADED if degraded else StepStatus.DONE
        rec.finished_at, rec.summary = now, dict(summary)
        self.updated_at = self.heartbeat_at = now
        nxt = STEP_ORDER.index(step) + 1
        if nxt >= len(STEP_ORDER):
            self._finish(RunStatus.DONE, now)
            return
        self.step = STEP_ORDER[nxt]
        nrec = self._record(self.step)
        nrec.status, nrec.started_at = StepStatus.RUNNING, now

    def record_progress(self, step: RunStep, generation: int, done: int, total: int, now: datetime) -> None:
        """Update the progress counters of the current step (fenced)."""
        self._fence(generation)
        self._need(RunStatus.RUNNING)
        if step is not self.step:
            raise InvalidStateTransition("not_current_step", extra={"step": step.value})
        rec = self._record(step)
        rec.done, rec.total = done, total
        self.heartbeat_at = self.updated_at = now

    def degrade(self, step: RunStep, reason: str, generation: int) -> None:
        """Remember that ``step`` produced less than full output; the run becomes ``partial``."""
        self._fence(generation)
        self._need(RunStatus.RUNNING)
        self.degraded.append(Degradation(step, reason))
        self.partial = True

    def fail(self, failure: RunFailure, step: RunStep | None, now: datetime) -> None:
        """``queued|running`` -> ``failed``."""
        self._need(RunStatus.QUEUED, RunStatus.RUNNING)
        self.error_code, self.error_step = failure.value, step
        self._finish(RunStatus.FAILED, now)

    def request_cancel(self, now: datetime) -> None:
        """``queued`` -> ``cancelled`` at once; ``running`` only records the request."""
        self._need(RunStatus.QUEUED, RunStatus.RUNNING)
        self.cancel_requested_at = now
        if self.status is RunStatus.QUEUED:
            self._finish(RunStatus.CANCELLED, now)
        else:
            self.updated_at = now

    def cancel(self, generation: int, now: datetime) -> None:
        """``running`` -> ``cancelled`` (the execution noticed the request)."""
        self._fence(generation)
        self._need(RunStatus.RUNNING)
        self._finish(RunStatus.CANCELLED, now)

    # ---- derived -----------------------------------------------------------------------------------------------
    @property
    def phase(self) -> str:
        """Plain-language phase for the progress UI."""
        if self.status is RunStatus.QUEUED:
            return "queued"
        if self.status is not RunStatus.RUNNING or self.step is None:
            return self.status.value
        return _PHASES[self.step]

    def percent(self) -> int:
        """Progress 0-100 from the step weights; never decreases within a run."""
        if self.status is RunStatus.DONE:
            return 100
        if self.step is None:
            return 0
        pct = sum(STEP_WEIGHTS[s] for s in STEP_ORDER[: STEP_ORDER.index(self.step)])
        rec = self.step_state.get(self.step)
        if rec is not None and rec.total > 0:
            pct += STEP_WEIGHTS[self.step] * min(rec.done, rec.total) // rec.total
        return min(pct, 99)

    @property
    def active(self) -> bool:
        """Queued or running."""
        return self.status in (RunStatus.QUEUED, RunStatus.RUNNING)

    def spent_usd(self) -> Decimal:
        """Money spent so far."""
        return self.spend.cost_usd
