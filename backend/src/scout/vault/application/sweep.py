"""``SweepVault``: the recurring ``vault.sweep`` job that recovers lost or stuck work."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.ports.jobs import JobQueuePort, JobSpec
from scout.shared import Clock
from scout.vault.application.ingest import fail_document
from scout.vault.ports.repos import VaultUnitOfWork

log = structlog.get_logger()


class SweepVault:
    """Re-enqueue stale uploads, unfinished purges and deletions; fail stalled ingestion; reschedule itself."""

    def __init__(
        self,
        uow: Callable[[], VaultUnitOfWork],
        jobs: JobQueuePort,
        clock: Clock,
        interval_minutes: int = 10,
        stalled_after_minutes: int = 60,
        limit: int = 100,
        provider_ready: Callable[[], bool] = lambda: False,
    ) -> None:
        self._provider_ready = provider_ready
        self._uow, self._jobs, self._clock = uow, jobs, clock
        self._interval, self._stalled, self._limit = interval_minutes, stalled_after_minutes, limit

    async def _enqueue(self, kind: str, document_id: str, account_id: str | None, attempts: int) -> None:
        await self._jobs.enqueue(
            JobSpec(
                kind=kind,
                payload={"account_id": account_id, "feature": kind, "document_id": document_id},
                dedupe_key=document_id,
                max_attempts=attempts,
            )  # fmt: skip
        )

    async def ensure_scheduled(self) -> None:
        """Queue the next sweep (deduplicated per time slot, so calling this twice queues one job)."""
        now = self._clock.now()
        period = timedelta(minutes=self._interval)
        slot = int(now.timestamp() // period.total_seconds()) + 1
        await self._jobs.enqueue(
            JobSpec(kind="vault.sweep", run_at=now + period, dedupe_key=f"vault.sweep:{slot}", max_attempts=3)
        )

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> dict[str, JsonValue]:
        """One sweep. The next one is scheduled first, so a failure below never ends the chain."""
        now = self._clock.now()
        period = timedelta(minutes=self._interval)
        await self.ensure_scheduled()
        async with self._uow() as uow:
            stale = await uow.documents.list_stale(
                now - period, now - timedelta(minutes=self._stalled), self._limit
            )
            owners: dict[UUID, str | None] = dict.fromkeys(
                (*stale.uploaded, *stale.purge, *stale.deleting, *stale.stalled, *stale.awaiting)
            )
            for doc_id in owners:
                doc = await uow.documents.get_for_update(doc_id)
                owners[doc_id] = str(doc.owner_id) if doc else None
        for doc_id in stale.uploaded:
            await self._enqueue("vault.ingest", str(doc_id), owners[doc_id], 4)
        for doc_id in stale.purge:
            await self._enqueue("vault.ingest", str(doc_id), owners[doc_id], 4)
        requeued = 0
        if self._provider_ready():  # an authorized endpoint exists now: analyse the parked documents
            for doc_id in stale.awaiting:
                await self._enqueue("vault.ingest", str(doc_id), owners[doc_id], 4)
                requeued += 1
        for doc_id in stale.deleting:
            await self._enqueue("vault.delete", str(doc_id), owners[doc_id], 8)
        failed = 0
        for doc_id in stale.stalled:
            failed += await fail_document(self._uow, self._clock, doc_id, "ingest_stalled")
        counts: dict[str, JsonValue] = {
            "uploaded": len(stale.uploaded), "purge": len(stale.purge), "deleting": len(stale.deleting),
            "stalled": failed, "awaiting_requeued": requeued,
        }  # fmt: skip
        log.info("vault.sweep", **counts)
        return counts
