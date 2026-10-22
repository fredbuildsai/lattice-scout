"""``DeleteAccountJob``: erase an account and everything it owns, in an order that survives crashes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.identity.domain.erasure import subject_hash
from scout.identity.ports.erasure import (
    AccountFinalizer,
    AuthAdminPort,
    MatchErasurePort,
    ObservabilityErasure,
    SubjectInventory,
)
from scout.ports.audit import AuditEntry
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.storage import FileStoragePort
from scout.shared import Clock, IdGenerator, InfrastructureError

log = structlog.get_logger()


class DeleteAccountJob:
    """Handler of ``account.delete``. Every step is idempotent, so a retry restarts from the top.

    The payload carries ``subject_id`` (never ``account_id``, which the worker would turn into log context) and
    ``pass``. Pass 1 enqueues pass 2, which sweeps late writers.
    """

    def __init__(
        self, *, inventory: SubjectInventory, matching: MatchErasurePort, storage: FileStoragePort,
        auth_admin: AuthAdminPort, observability: ObservabilityErasure, finalizer: AccountFinalizer,
        jobs: JobQueuePort, clock: Clock, ids: IdGenerator, audit_key: str, second_pass_minutes: int = 15,
    ) -> None:  # fmt: skip
        self._inventory, self._matching, self._storage = inventory, matching, storage
        self._auth, self._obs, self._finalizer = auth_admin, observability, finalizer
        self._jobs, self._clock, self._ids = jobs, clock, ids
        self._key, self._second_pass = audit_key, timedelta(minutes=second_pass_minutes)

    async def handle(
        self, job_id: UUID, payload: dict[str, Any], *, last_attempt: bool = False
    ) -> dict[str, JsonValue]:
        """Run one pass.

        Raises:
            InfrastructureError: a step failed or the bucket still lists objects; the worker retries.
        """
        raw = payload.get("subject_id")
        if raw is None:
            return {"skipped": "done"}
        account_id = UUID(str(raw))
        pass_no = int(payload.get("pass", 1))
        h = subject_hash(self._key, account_id)
        now = self._clock.now()
        subject = await self._inventory.collect(account_id)
        carried = {UUID(str(x)) for x in payload.get("sessions", [])}
        if carried:  # pass 1 deleted app_session; a late writer may have recreated it without an account
            subject = subject.model_copy(update={"session_ids": subject.session_ids | carried})

        forgotten = await self._matching.forget(account_id, now)
        prefix = f"vault/{account_id}"
        removed = await self._storage.delete_prefix(prefix)
        if await self._storage.list_prefix(prefix):
            raise InfrastructureError("storage still lists objects after the purge")
        await self._delete_auth_user(account_id, h, last_attempt)
        observed = await self._obs.erase(subject)
        if pass_no == 1:
            await self._enqueue_second_pass(account_id, h, now, subject.session_ids)
        counts = {
            "files": removed,
            "proposals_detached": forgotten.detached,
            "proposals_deleted": forgotten.deleted,
        }
        counts.update(observed)
        entry = AuditEntry(
            id=self._ids.new(), actor_id=None, action="account.deleted", target=None, at=now,
            meta={"subject_hash": h, "counts": dict(counts)},
        )  # fmt: skip
        counts.update(await self._finalizer.finish(subject, job_id, entry))
        return {"subject_hash": h, "pass": pass_no, **counts}

    async def _delete_auth_user(self, account_id: UUID, h: str, last_attempt: bool) -> None:
        try:
            await self._auth.delete_user(account_id)
        except Exception:
            if last_attempt:
                log.error("identity.auth_delete_failed", subject_hash=h)
            raise

    async def _enqueue_second_pass(
        self, account_id: UUID, h: str, now: Any, session_ids: frozenset[UUID] = frozenset()
    ) -> None:
        await self._jobs.enqueue(
            JobSpec(
                kind="account.delete",
                payload={
                    "subject_id": str(account_id),
                    "pass": 2,
                    "feature": "account.delete",
                    "sessions": sorted(str(i) for i in session_ids),  # rewritten away when pass 2 finishes
                },
                run_at=now + self._second_pass,
                dedupe_key=f"erase:{h}:2",
                max_attempts=10,
            )
        )


class StuckErasures(Protocol):
    """Accounts whose deletion was requested a while ago and never finished."""

    async def stuck(self, deleted_before: datetime, limit: int) -> list[UUID]:
        """Ids of soft-deleted accounts still in the database."""
        ...


class SweepErasures:
    """Job ``identity.sweep`` (hourly): re-enqueue pass 1 for deletions whose job was lost."""

    KIND = "identity.sweep"

    def __init__(
        self, stuck: StuckErasures, jobs: JobQueuePort, clock: Clock, audit_key: str, grace_minutes: int = 15
    ) -> None:
        self._stuck, self._jobs, self._clock, self._key = stuck, jobs, clock, audit_key
        self._grace = timedelta(minutes=grace_minutes)

    async def ensure_scheduled(self) -> None:
        """Queue the next sweep (one per hour slot)."""
        now = self._clock.now()
        slot = int(now.timestamp() // 3600) + 1
        await self._jobs.enqueue(
            JobSpec(
                kind=self.KIND,
                run_at=now + timedelta(hours=1),
                dedupe_key=f"identity.sweep:{slot}",
                max_attempts=3,
            )
        )

    async def handle(
        self, payload: dict[str, JsonValue], *, last_attempt: bool = False
    ) -> dict[str, JsonValue]:
        """Schedule the next sweep first, then re-enqueue stuck erasures."""
        await self.ensure_scheduled()
        ids = await self._stuck.stuck(self._clock.now() - self._grace, 100)
        for account_id in ids:
            h = subject_hash(self._key, account_id)
            await self._jobs.enqueue(
                JobSpec(
                    kind="account.delete",
                    payload={"subject_id": str(account_id), "pass": 1, "feature": "account.delete"},
                    dedupe_key=f"erase:{h}:1",
                    max_attempts=10,
                )
            )
        return {"requeued": len(ids)}
