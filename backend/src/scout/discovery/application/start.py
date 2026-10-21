"""``StartAnalysisRun``: validate the request, apply the limits, insert the run and queue ``discovery.run``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import structlog

from scout.discovery.domain.budget import RunBudget
from scout.discovery.domain.run import AnalysisRun, RunDocument
from scout.discovery.ports.repos import DiscoveryUnitOfWork, RunInProgress
from scout.discovery.ports.sources import ProfileSource, VaultSource
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.shared import AccountId, Clock, Conflict, IdGenerator, NotFound, RateLimited, ValidationFailed

log = structlog.get_logger()
MAX_DOCUMENTS = 10


class ProfileRequired(Conflict):
    """A run needs a claimed profile whose import has finished."""

    code = "profile_required"
    title = "Claim your profile first"


class DocumentNotReady(ValidationFailed):
    """An attached document is still being processed (or failed)."""

    code = "document_not_ready"
    title = "A document is not ready yet"


class RunLimitReached(RateLimited):
    """Too many runs in 24 hours."""

    code = "run_limit_reached"
    title = "Daily run limit reached"


class DailyBudgetReached(RateLimited):
    """Not enough of today's budget is left for a useful run."""

    code = "daily_budget_reached"
    title = "Daily analysis budget reached"


@dataclass(frozen=True)
class StartConfig:
    """Limits applied when a run starts."""

    run_budget_usd: Decimal = Decimal("1.50")
    account_daily_budget_usd: Decimal = Decimal("5.00")
    max_runs_per_day: int = 5
    openalex_calls: int = 400
    max_corpus: int = 2500


class StartAnalysisRun:
    """Create a queued run for the owner."""

    def __init__(
        self,
        uow: Callable[[], DiscoveryUnitOfWork],
        jobs: JobQueuePort,
        profiles: ProfileSource,
        vault: VaultSource,
        clock: Clock,
        ids: IdGenerator,
        cfg: StartConfig,
    ) -> None:
        self._uow, self._jobs, self._profiles, self._vault = uow, jobs, profiles, vault
        self._clock, self._ids, self._cfg = clock, ids, cfg

    async def start(
        self, owner_id: AccountId, document_ids: Sequence[UUID], session_id: UUID | None = None
    ) -> AnalysisRun:
        """Insert a ``queued`` run and enqueue its job.

        Raises:
            ValidationFailed: more than 10 documents, or duplicates.
            NotFound: a document is not the owner's (or is being deleted).
            DocumentNotReady: a document is not ``ready`` yet.
            ProfileRequired: no claimed profile with a finished import.
            RunInProgress: the owner already has an active run.
            RunLimitReached, DailyBudgetReached: the daily caps.
        """
        ids = tuple(document_ids)
        if len(ids) > MAX_DOCUMENTS or len(set(ids)) != len(ids):
            raise ValidationFailed("Attach at most 10 different documents", extra={"max": MAX_DOCUMENTS})
        checks = await self._vault.check_documents(owner_id, ids) if ids else []
        for chk in checks:
            if not chk.exists:
                raise NotFound("Document not found")
        for chk in checks:
            if not chk.ready:
                raise DocumentNotReady(
                    "Wait until the document is ready", extra={"document_id": str(chk.document_id)}
                )
        profile = await self._profiles.run_profile(owner_id)
        if profile is None or not profile.import_done:
            raise ProfileRequired("Claim your profile and wait for its import to finish")

        now = self._clock.now()
        since = now - timedelta(hours=24)
        async with self._uow() as uow:
            active = await uow.runs.active_for(owner_id)
            if active is not None:
                raise RunInProgress("A run is already in progress", extra={"run_id": str(active.id)})
            if await uow.runs.count_started_since(owner_id, since) >= self._cfg.max_runs_per_day:
                raise RunLimitReached("Daily run limit reached")
            left = self._cfg.account_daily_budget_usd - await uow.runs.spend_since(owner_id, since)
        usd = min(self._cfg.run_budget_usd, left)
        reserve = RunBudget(usd=Decimal(1))
        if usd < reserve.reserve_reasoning + 3 * reserve.reserve_fast:
            raise DailyBudgetReached("Today's analysis budget is used up")

        run = AnalysisRun(
            id=self._ids.new(), owner_id=owner_id, created_at=now, session_id=session_id,
            budget=RunBudget(usd=usd, openalex_calls=self._cfg.openalex_calls, max_corpus=self._cfg.max_corpus),
        )  # fmt: skip
        from dataclasses import replace

        run.inputs = replace(
            run.inputs,
            documents=tuple(
                RunDocument(f"d{i}", chk.document_id, chk.retention) for i, chk in enumerate(checks, start=1)
            ),
        )
        async with self._uow() as uow:
            await uow.runs.add(run)  # RunInProgress when a parallel start won the race
            await uow.commit()
        try:
            await self._jobs.enqueue(
                JobSpec(
                    kind="discovery.run",
                    payload={
                        "account_id": str(owner_id),
                        "session_id": str(session_id) if session_id else None,
                        "feature": "discovery.run",
                        "run_id": str(run.id),
                    },
                    dedupe_key=str(run.id),
                    max_attempts=5,
                )  # fmt: skip
            )
        except Exception:
            log.error("discovery.enqueue_failed", run_id=str(run.id))  # the sweeper re-enqueues queued runs
        return run
