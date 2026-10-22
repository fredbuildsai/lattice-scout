"""M6 slice A: ``DeleteAccountJob`` against in-memory fakes (plan M6 task 2; ARCHITECTURE sections 7, 10;
docs/design/M6-deletion.md section 3 "DeleteAccountJob.handle" steps 0-7, section 5 "Unit (fakes)", section 8;
decisions 88, 91, 92).

Assumed API (``scout.identity.application.erasure.DeleteAccountJob``). The constructor is not fixed by the design
note, so the single place that builds it is ``_World.job()`` below; adapt only that function if the keyword names
differ. The contract under test is ``await job.handle(job_id, payload, *, last_attempt) -> dict[str, JsonValue]``:

0. payload without ``subject_id`` -> ``{"skipped": "done"}`` and no side effect;
1. ``MatchErasurePort.forget`` (before the account row is deleted);
2. ``FileStoragePort.delete_prefix("vault/{id}")`` then ``list_prefix`` must be empty, else raise;
3. ``AuthAdminPort.delete_user`` (errors raise; on the last attempt log ``identity.auth_delete_failed`` with the
   hash only, then raise);
4. ``ObservabilityErasure.erase``;
5. pass 1 only: enqueue pass 2 (``run_at = now + 15 min``, dedupe ``erase:{hash}:2``) BEFORE step 6;
6. ``AccountFinalizer.finish(subject, own_job_id, entry)`` with ``entry`` = audit ``account.deleted`` (actor and
   target None, ``meta`` holds ``subject_hash`` and ``counts``, no e-mail);
7. result contains ``subject_hash`` and ``pass`` and no id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from structlog.testing import capture_logs
from tests.fakes.erasure import (
    FakeAccountFinalizer,
    FakeAuthAdmin,
    FakeMatchErasure,
    FakeObservabilityErasure,
    FakeSubjectInventory,
    TracingQueue,
    TracingStorage,
)

from scout.identity.application.erasure import DeleteAccountJob
from scout.identity.domain.erasure import ErasureSubject, subject_hash
from scout.ports.jobs import Job, JobSpec, JobStatus
from scout.shared import AccountId, FixedClock, InfrastructureError, SequentialIdGenerator
from scout.worker.loop import _context_from

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
KEY = "unit-test-audit-key"
A = AccountId(UUID(int=0xA0))
B = AccountId(UUID(int=0xB0))
EMAIL = "ada@example.edu"
SECOND_PASS = timedelta(minutes=15)
H = subject_hash(KEY, A)

SUBJECT = ErasureSubject(
    account_id=A,
    subject_hash=H,
    researcher_id=UUID(int=0xA1),
    document_ids=frozenset({UUID(int=0xA2)}),
    run_ids=frozenset({UUID(int=0xA3)}),
    opportunity_ids=frozenset({UUID(int=0xA4)}),
    session_ids=frozenset({UUID(int=0xA5)}),
    mutual_match_ids=frozenset({UUID(int=0xA6)}),
)
RAW_IDS = [str(A), *SUBJECT.needles()]


@dataclass
class _World:
    """Everything the job touches, wired with fakes that share one trace."""

    trace: list[str] = field(default_factory=list)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))

    def __post_init__(self) -> None:
        ids = SequentialIdGenerator(1000)
        self.queue = TracingQueue(self.clock, ids, trace=self.trace)
        self.storage = TracingStorage(self.trace)
        self.inventory = FakeSubjectInventory(KEY, {A: SUBJECT}, self.trace)
        self.matching = FakeMatchErasure(trace=self.trace)
        self.auth = FakeAuthAdmin(self.trace)
        self.observability = FakeObservabilityErasure(self.trace)
        self.finalizer = FakeAccountFinalizer(self.queue, self.trace)
        self.finalizer.accounts.add(A)
        self.ids = ids

    def job(self) -> DeleteAccountJob:
        return DeleteAccountJob(
            inventory=self.inventory,
            matching=self.matching,
            storage=self.storage,
            auth_admin=self.auth,
            observability=self.observability,
            finalizer=self.finalizer,
            jobs=self.queue,
            clock=self.clock,
            ids=self.ids,
            audit_key=KEY,
            second_pass_minutes=15,
        )

    async def seed_storage(self) -> None:
        await self.storage.put(f"vault/{A}/doc-one", b"x", "application/pdf")
        await self.storage.put(f"vault/{A}/orphan", b"y", "application/pdf")
        await self.storage.put(f"vault/{B}/doc-b", b"z", "application/pdf")

    async def start(self, pass_no: int = 1) -> Job:
        """Enqueue and claim the ``account.delete`` job of ``pass_no`` as the API/worker would."""
        await self.queue.enqueue(
            JobSpec(
                kind="account.delete",
                payload={"subject_id": str(A), "pass": pass_no, "feature": "account.delete"},
                dedupe_key=f"erase:{H}:{pass_no}",
                max_attempts=10,
                run_at=self.clock.now(),
            )
        )
        job = await self.queue.claim("w", ["account.delete"])
        assert job is not None
        self.trace.clear()
        return job

    async def run(self, job: Job, *, last_attempt: bool = False) -> dict:
        return await self.job().handle(job.id, job.payload, last_attempt=last_attempt)

    def pass2_jobs(self) -> list[Job]:
        return [
            j for j in self.queue.jobs.values() if j.kind == "account.delete" and j.payload.get("pass") == 2
        ]

    def state(self) -> dict:
        """What must be identical whether or not a step failed on the way."""
        return {
            "audit": [e.action for e in self.finalizer.audit_rows],
            "accounts": sorted(self.finalizer.accounts),
            "storage": sorted(self.storage.objects),
            "pass2": len(self.pass2_jobs()),
            "auth": sorted(set(self.auth.deleted)),
        }


# ------------------------------------------------------------------ pass 1, happy path


async def test_pass_one_runs_the_steps_in_order() -> None:
    w = _World()
    await w.seed_storage()
    job = await w.start()
    await w.run(job)
    assert w.trace == [
        "inventory.collect",
        "match.forget",
        "storage.delete_prefix",
        "storage.list_prefix",
        "auth.delete_user",
        "obs.erase",
        "queue.enqueue:account.delete",  # pass 2, scheduled BEFORE the point of no return
        "finalizer.finish",
    ]


async def test_matching_is_forgotten_before_the_account_row_is_deleted() -> None:
    w = _World()
    job = await w.start()
    await w.run(job)
    assert w.trace.index("match.forget") < w.trace.index("finalizer.finish")
    assert w.matching.forgotten == [A] and w.matching.nows == [NOW]


async def test_storage_prefix_is_purged_and_other_users_files_stay() -> None:
    w = _World()
    await w.seed_storage()
    await w.run(await w.start())
    assert w.storage.purged_prefixes == [f"vault/{A}"]
    assert sorted(w.storage.objects) == [f"vault/{B}/doc-b"]


async def test_auth_user_is_deleted_and_account_is_gone() -> None:
    w = _World()
    await w.run(await w.start())
    assert w.auth.deleted == [A]
    assert A not in w.finalizer.accounts
    assert w.observability.erased == [SUBJECT]


async def test_audit_entry_is_a_pii_free_tombstone() -> None:
    w = _World()
    await w.run(await w.start())
    (entry,) = w.finalizer.audit_rows
    assert entry.action == "account.deleted" and entry.actor_id is None and entry.target is None
    assert entry.meta["subject_hash"] == H and "counts" in entry.meta
    blob = json.dumps(entry.meta) + entry.action
    assert not any(raw in blob for raw in RAW_IDS) and "example.edu" not in blob and EMAIL not in blob


async def test_pass_two_is_scheduled_once_with_hash_based_dedupe_and_subject_id_key() -> None:
    w = _World()
    await w.run(await w.start())
    (second,) = w.pass2_jobs()
    assert second.status is JobStatus.QUEUED
    assert second.run_at == NOW + SECOND_PASS
    assert second.payload.get("subject_id") == str(A) and "account_id" not in second.payload
    assert second.payload.get("feature") == "account.delete"
    assert (second.kind, f"erase:{H}:2") in w.queue.dedupe.values()
    assert not any(str(A) in key for _, key in w.queue.dedupe.values())  # no raw id in any dedupe key


async def test_own_payload_is_rewritten_without_ids_and_result_has_none() -> None:
    w = _World()
    job = await w.start()
    result = await w.run(job)
    assert w.queue.jobs[job.id].payload == {"subject_hash": H, "pass": 1}
    assert result["subject_hash"] == H and result["pass"] == 1
    blob = json.dumps(result)
    assert not any(raw in blob for raw in RAW_IDS)


async def test_nothing_about_the_user_is_logged_but_the_hash() -> None:
    w = _World()
    await w.seed_storage()
    job = await w.start()
    with capture_logs() as logs:
        await w.run(job)
    blob = json.dumps(logs, default=str)
    assert not any(raw in blob for raw in RAW_IDS) and EMAIL not in blob


async def test_job_context_never_attributes_the_work_to_the_deleted_account() -> None:
    w = _World()
    job = await w.start()
    ctx = _context_from(job)
    assert ctx.account_id is None and ctx.feature == "account.delete"
    await w.run(job)
    (second,) = w.pass2_jobs()
    assert _context_from(second).account_id is None


# ------------------------------------------------------------------ pass 2 and finished jobs


async def test_pass_two_sweeps_late_writers_and_enqueues_no_third_pass() -> None:
    w = _World()
    await w.seed_storage()
    first = await w.start()
    await w.queue.complete(first.id, await w.run(first))
    await w.storage.put(f"vault/{A}/late-upload", b"late", "application/pdf")  # an in-flight upload landed
    w.clock.advance(SECOND_PASS)
    second = await w.queue.claim("w", ["account.delete"])
    assert second is not None and second.payload["pass"] == 2
    w.trace.clear()
    result = await w.job().handle(second.id, second.payload, last_attempt=False)
    assert result["pass"] == 2 and result["subject_hash"] == H
    assert f"vault/{A}/late-upload" not in w.storage.objects
    assert w.trace.count("queue.enqueue:account.delete") == 0
    assert not [j for j in w.queue.jobs.values() if j.payload.get("pass") == 3]
    assert [e.action for e in w.finalizer.audit_rows] == ["account.deleted"]  # still exactly one tombstone
    assert w.queue.jobs[second.id].payload == {"subject_hash": H, "pass": 2}


async def test_a_finished_job_is_skipped_without_touching_anything() -> None:
    w = _World()
    job = await w.start()
    await w.run(job)
    w.trace.clear()
    done_payload = w.queue.jobs[job.id].payload
    assert "subject_id" not in done_payload
    out = await w.job().handle(job.id, done_payload, last_attempt=False)
    assert out == {"skipped": "done"}
    assert w.trace == []


async def test_a_vanished_account_is_still_erased_from_the_bucket_and_auth() -> None:
    w = _World()
    w.inventory.subjects.clear()  # the account row is already gone: the inventory returns a bare subject
    w.finalizer.accounts.clear()
    await w.seed_storage()
    result = await w.run(await w.start())
    assert result["subject_hash"] == H
    assert sorted(w.storage.objects) == [f"vault/{B}/doc-b"]
    assert w.auth.deleted == [A] and [e.action for e in w.finalizer.audit_rows] == ["account.deleted"]


# ------------------------------------------------------------------ failures and retries


async def test_leftover_objects_after_the_purge_make_the_job_fail_before_anything_irreversible() -> None:
    w = _World()
    await w.seed_storage()
    w.storage.stubborn = True
    job = await w.start()
    with pytest.raises(Exception):  # noqa: B017 - any error type: the job must retry
        await w.run(job)
    assert A in w.finalizer.accounts and w.finalizer.audit_rows == [] and w.auth.deleted == []


async def test_auth_failure_raises_and_keeps_the_soft_deleted_account() -> None:
    w = _World()
    w.auth.fail_next()
    with pytest.raises(InfrastructureError):
        await w.run(await w.start())
    assert A in w.finalizer.accounts and w.finalizer.audit_rows == []


async def test_auth_failure_on_the_last_attempt_logs_the_hash_only_and_still_raises() -> None:
    w = _World()
    w.auth.fail_next()
    job = await w.start()
    with capture_logs() as logs, pytest.raises(InfrastructureError):
        await w.run(job, last_attempt=True)
    (event,) = [e for e in logs if e.get("event") == "identity.auth_delete_failed"]
    blob = json.dumps(event, default=str)
    assert H in blob and not any(raw in blob for raw in RAW_IDS)
    assert A in w.finalizer.accounts


async def test_auth_failure_before_the_last_attempt_does_not_log_the_final_failure() -> None:
    w = _World()
    w.auth.fail_next()
    job = await w.start()
    with capture_logs() as logs, pytest.raises(InfrastructureError):
        await w.run(job, last_attempt=False)
    assert not [e for e in logs if e.get("event") == "identity.auth_delete_failed"]


@pytest.mark.parametrize(
    "step",
    [
        "inventory",
        "matching",
        "storage_delete",
        "storage_list",
        "auth",
        "observability",
        "finalizer",
        "queue",
    ],
)
async def test_a_failure_at_any_step_then_a_retry_ends_in_the_same_state_as_a_clean_run(step: str) -> None:
    clean = _World()
    await clean.seed_storage()
    await clean.run(await clean.start())

    w = _World()
    await w.seed_storage()
    job = await w.start()
    {
        "inventory": w.inventory.fail_next,
        "matching": w.matching.fail_next,
        "storage_delete": lambda: w.storage.fail_next("delete_prefix"),
        "storage_list": lambda: w.storage.fail_next("list_prefix"),
        "auth": w.auth.fail_next,
        "observability": w.observability.fail_next,
        "finalizer": w.finalizer.fail_next,
        "queue": lambda: None,
    }[step]()
    if step == "queue":
        original = w.queue.enqueue
        calls = {"n": 0}

        async def flaky(spec: JobSpec) -> UUID:
            calls["n"] += 1
            if calls["n"] == 1:
                raise InfrastructureError("queue unavailable")
            return await original(spec)

        w.queue.enqueue = flaky  # type: ignore[method-assign,assignment]

    with pytest.raises(InfrastructureError):
        await w.run(job)
    assert A in w.finalizer.accounts  # nothing irreversible happened, or the retry will finish it
    await w.run(job)  # the retry restarts from step 0 with the same payload

    assert w.state() == clean.state()
    assert [e.action for e in w.finalizer.audit_rows] == ["account.deleted"]
    assert len(w.pass2_jobs()) == 1


async def test_running_the_same_pass_twice_is_idempotent() -> None:
    w = _World()
    await w.seed_storage()
    job = await w.start()
    await w.run(job)
    first = w.state()
    await w.job().handle(
        job.id, {"subject_id": str(A), "pass": 1, "feature": "account.delete"}, last_attempt=False
    )
    assert w.state() == first
    assert [e.action for e in w.finalizer.audit_rows] == ["account.deleted"]


async def test_pass_two_carries_the_session_ids_so_a_recreated_session_is_found() -> None:
    w = _World()
    first = await w.start()
    await w.queue.complete(first.id, await w.run(first))
    (second,) = w.pass2_jobs()
    assert second.payload["sessions"] == [str(i) for i in sorted(SUBJECT.session_ids)]
    late = UUID(int=0xFEED)  # a session the late writer recreated without an account
    payload = {**second.payload, "sessions": [*second.payload["sessions"], str(late)]}
    w.observability.erased.clear()
    await w.job().handle(second.id, payload)
    assert (
        late in w.observability.erased[0].session_ids
        and SUBJECT.session_ids <= w.observability.erased[0].session_ids
    )


async def test_a_dead_erasure_row_with_the_raw_id_is_removed_when_a_later_pass_finishes() -> None:
    w = _World()
    dead = await w.start()
    w.queue.jobs[dead.id] = dead.model_copy(
        update={"status": JobStatus.DEAD, "last_error": f"auth failed for {A}"}
    )
    w.queue.dedupe.pop(dead.id, None)
    again = await w.queue.enqueue(
        JobSpec(kind="account.delete", payload={"subject_id": str(A), "pass": 1}, dedupe_key="erase:retry:1")
    )
    assert again is not None
    job = await w.queue.claim("w", ["account.delete"])
    assert job is not None and job.id != dead.id
    await w.run(job)
    assert dead.id not in w.queue.jobs
