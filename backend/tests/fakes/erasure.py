"""In-memory doubles for the M6 account-erasure ports (docs/design/M6-deletion.md section 3 "Ports" and section 5).

Every fake appends a short step name to a shared ``trace`` list (so tests can assert the order of the erasure
steps) and supports ``fail_next(n)`` (the next ``n`` calls raise ``InfrastructureError``).

Ports faked (``scout.identity.ports.erasure``): ``SubjectInventory``, ``MatchErasurePort``,
``ObservabilityErasure``, ``AccountFinalizer``, ``AuthAdminPort``. ``ForgetResult`` is defined here as a plain
frozen dataclass with ``detached`` and ``deleted``; the job only needs those two attributes (structural typing).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from scout.identity.domain.erasure import ErasureSubject, subject_hash
from scout.ports.audit import AuditEntry
from scout.ports.jobs import JobSpec, JobStatus
from scout.shared import InfrastructureError
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.storage import InMemoryFileStorage


@dataclass(frozen=True)
class ForgetResult:
    """Counts returned by the matching erasure."""

    detached: int
    deleted: int


class _Scripted:
    """``fail_next(n)`` plus a shared step trace."""

    def __init__(self, trace: list[str] | None = None) -> None:
        self.trace: list[str] = trace if trace is not None else []
        self._failures = 0
        self.calls = 0

    def fail_next(self, n: int = 1) -> None:
        """Make the next ``n`` calls raise."""
        self._failures = n

    def _step(self, name: str) -> None:
        self.calls += 1
        self.trace.append(name)
        if self._failures > 0:
            self._failures -= 1
            raise InfrastructureError(f"{name} unavailable")


class FakeSubjectInventory(_Scripted):
    """Returns the registered subject for an account, or a bare one (account id only) when it is gone."""

    def __init__(self, key: str, subjects: dict[UUID, ErasureSubject] | None = None, trace=None) -> None:
        super().__init__(trace)
        self.key, self.subjects = key, subjects or {}

    async def collect(self, account_id: UUID) -> ErasureSubject:
        self._step("inventory.collect")
        return self.subjects.get(account_id) or ErasureSubject(
            account_id=account_id, subject_hash=subject_hash(self.key, account_id)
        )


class FakeMatchErasure(_Scripted):
    """``forget`` records the call; the result is configurable."""

    def __init__(self, result: ForgetResult = ForgetResult(detached=1, deleted=1), trace=None) -> None:  # noqa: B008
        super().__init__(trace)
        self.result = result
        self.forgotten: list[UUID] = []
        self.nows: list[datetime] = []

    async def forget(self, account_id: UUID, now: datetime) -> ForgetResult:
        self._step("match.forget")
        self.forgotten.append(account_id)
        self.nows.append(now)
        return self.result


class FakeObservabilityErasure(_Scripted):
    """``erase`` records the subject and returns fixed counts."""

    def __init__(self, trace=None) -> None:
        super().__init__(trace)
        self.erased: list[ErasureSubject] = []

    async def erase(self, subject: ErasureSubject) -> dict[str, int]:
        self._step("obs.erase")
        self.erased.append(subject)
        return {"events_deleted": 3, "sessions_deleted": 1, "calls_anonymised": 4}


class FakeAuthAdmin(_Scripted):
    """Idempotent ``delete_user`` that remembers who was deleted (a repeat call is harmless)."""

    def __init__(self, trace=None) -> None:
        super().__init__(trace)
        self.deleted: list[UUID] = []

    async def delete_user(self, user_id: UUID) -> None:
        self._step("auth.delete_user")
        self.deleted.append(user_id)


class FakeAccountFinalizer(_Scripted):
    """Mimics the single finish transaction: jobs and audit rows go, one tombstone row, the account, own payload.

    ``accounts`` is the set of account ids still present; ``audit_rows`` holds the surviving audit entries. When
    a ``queue`` is given, jobs whose payload mentions a purge id are deleted (except ``account.delete``) and the
    own job's payload is rewritten to ``{subject_hash, pass}``.
    """

    def __init__(self, queue: InMemoryJobQueue | None = None, trace=None) -> None:
        super().__init__(trace)
        self.queue = queue
        self.accounts: set[UUID] = set()
        self.audit_rows: list[AuditEntry] = []

    async def finish(self, subject: ErasureSubject, own_job_id: UUID, entry: AuditEntry) -> dict[str, int]:
        self._step("finalizer.finish")
        jobs_deleted = 0
        if self.queue is not None:
            needles = subject.needles()
            for jid, job in list(self.queue.jobs.items()):
                blob = json.dumps(job.payload)
                dead_erasure = job.kind == "account.delete" and job.status is JobStatus.DEAD
                if (
                    jid != own_job_id
                    and (job.kind != "account.delete" or dead_erasure)
                    and any(n in blob for n in needles)
                ):
                    del self.queue.jobs[jid]
                    jobs_deleted += 1
            own = self.queue.jobs.get(own_job_id)
            if own is not None:
                self.queue.jobs[own_job_id] = own.model_copy(
                    update={
                        "payload": {"subject_hash": subject.subject_hash, "pass": own.payload.get("pass", 1)},
                        "last_error": None,
                    }
                )
        if not any(
            r.action == "account.deleted" and r.meta.get("subject_hash") == subject.subject_hash
            for r in self.audit_rows
        ):
            self.audit_rows.append(entry)
        self.accounts.discard(subject.account_id)
        return {"jobs_deleted": jobs_deleted, "audit_deleted": 0}


class TracingStorage(InMemoryFileStorage):
    """In-memory storage that traces the purge calls; ``stubborn`` makes ``delete_prefix`` silently keep objects."""

    def __init__(self, trace: list[str]) -> None:
        super().__init__()
        self.trace, self.stubborn = trace, False
        self.purged_prefixes: list[str] = []

    async def delete_prefix(self, prefix: str) -> int:
        self.trace.append("storage.delete_prefix")
        self.purged_prefixes.append(prefix)
        if self.stubborn:
            return 0
        return await super().delete_prefix(prefix)

    async def list_prefix(self, prefix: str) -> list[str]:
        self.trace.append("storage.list_prefix")
        return await super().list_prefix(prefix)


class TracingQueue(InMemoryJobQueue):
    """Job queue that traces ``enqueue`` and keeps every spec it was given."""

    def __init__(self, *args, trace: list[str], **kw) -> None:
        super().__init__(*args, **kw)
        self.trace = trace
        self.specs: list[JobSpec] = []

    async def enqueue(self, job: JobSpec) -> UUID:
        self.trace.append(f"queue.enqueue:{job.kind}")
        self.specs.append(job)
        return await super().enqueue(job)
