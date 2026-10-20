"""M2 vault, SweepVault recurring job (plan M2 test-first; design M2-vault-ingestion.md §5 "SweepVault", §10 invariants 2 and 7).

Covers: self-rescheduling with a slot dedupe key, re-enqueueing stale uploads, unfinished purges and stale deletions,
failing stalled documents (`ingest_stalled`) with purge for discard, per-run limit, and leaving healthy rows alone.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from tests.unit.vault._env import OWNER, VaultEnv, drain, make_pages, pdf_bytes

from scout.ports.jobs import Job
from scout.shared import InfrastructureError
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocStatus

MIN = timedelta(minutes=1)


def _jobs(env: VaultEnv, kind: str) -> list[Job]:
    return [j for j in env.jobs.jobs.values() if j.kind == kind]


async def _quiet_upload(env: VaultEnv, seed: int = 0, keep: bool = True) -> Document:
    """Upload whose ingest job is lost (as after a crash between commit and enqueue)."""
    env.jobs.fail_enqueue = True
    try:
        return await env.upload(pdf_bytes(make_pages(2, seed=seed)), keep=keep)
    finally:
        env.jobs.fail_enqueue = False


# --------------------------------------------------------------------------- scheduling
async def test_sweep_reschedules_itself_with_a_slot_dedupe_key() -> None:
    env = VaultEnv()
    await env.sweep().handle({}, last_attempt=False)
    (job,) = _jobs(env, "vault.sweep")
    assert job.run_at == env.clock.now() + 10 * MIN
    key = env.jobs.dedupe[job.id]
    assert key[0] == "vault.sweep" and key[1].startswith("vault.sweep:")
    await env.sweep().handle({}, last_attempt=False)
    assert len(_jobs(env, "vault.sweep")) == 1  # same slot, same key


async def test_interval_is_configurable() -> None:
    env = VaultEnv()
    await env.sweep(interval_minutes=3).handle({}, last_attempt=False)
    (job,) = _jobs(env, "vault.sweep")
    assert job.run_at == env.clock.now() + 3 * MIN


async def test_nothing_to_do_returns_a_result_and_enqueues_only_the_next_sweep() -> None:
    env = VaultEnv()
    out = await env.sweep().handle({}, last_attempt=False)
    assert isinstance(out, dict)
    assert [j.kind for j in env.jobs.jobs.values()] == ["vault.sweep"]


# --------------------------------------------------------------------------- stale uploads
async def test_stale_uploaded_document_gets_a_deduped_ingest_job_that_completes_it() -> None:
    env = VaultEnv()
    doc = await _quiet_upload(env)
    env.clock.advance(5 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert not _jobs(env, "vault.ingest")  # younger than 10 minutes: the original job may still be coming
    env.clock.advance(6 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    await env.sweep().handle({}, last_attempt=False)
    (job,) = _jobs(env, "vault.ingest")
    assert job.payload["document_id"] == str(doc.id) and env.jobs.dedupe[job.id] == (
        "vault.ingest",
        str(doc.id),
    )
    assert job.max_attempts >= 1
    await drain(env, {"vault.ingest": env.ingest_uc().handle})
    assert env.doc(doc.id).status is DocStatus.READY


async def test_unfinished_purge_of_a_discarded_document_is_resumed() -> None:
    env = VaultEnv()
    doc = await _quiet_upload(env, keep=False)
    env.storage.fail_next("delete")
    with pytest.raises(InfrastructureError):
        await env.ingest(doc)
    assert await env.storage.exists(doc.storage_key)
    env.clock.advance(11 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert len(_jobs(env, "vault.ingest")) == 1
    await drain(env, {"vault.ingest": env.ingest_uc().handle})
    assert not await env.storage.exists(doc.storage_key)
    assert len(env.audit_rows("document.original_discarded")) == 1


async def test_healthy_documents_are_left_alone() -> None:
    env = VaultEnv()
    kept = await _quiet_upload(env, seed=1, keep=True)
    discarded = await _quiet_upload(env, seed=2, keep=False)
    await env.ingest(kept)
    await env.ingest(discarded)
    snap = (env.snapshot(kept.id), env.snapshot(discarded.id))
    env.clock.advance(3 * 60 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert not _jobs(env, "vault.ingest") and not _jobs(env, "vault.delete")
    assert (env.snapshot(kept.id), env.snapshot(discarded.id)) == snap


async def test_limit_caps_the_rows_handled_per_run() -> None:
    env = VaultEnv()
    for seed in range(5):
        await _quiet_upload(env, seed=seed)
    env.clock.advance(11 * MIN)
    await env.sweep(limit=2).handle({}, last_attempt=False)
    assert len(_jobs(env, "vault.ingest")) == 2


# --------------------------------------------------------------------------- stale deletions
async def test_stale_deleting_rows_get_a_delete_job_and_young_ones_do_not() -> None:
    env = VaultEnv()
    doc = await _quiet_upload(env)
    env.jobs.fail_enqueue = True
    await env.service().delete(OWNER, doc.id)
    env.jobs.fail_enqueue = False
    env.clock.advance(5 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert not _jobs(env, "vault.delete")
    env.clock.advance(6 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    await env.sweep().handle({}, last_attempt=False)
    (job,) = _jobs(env, "vault.delete")
    assert job.payload["document_id"] == str(doc.id) and env.jobs.dedupe[job.id] == (
        "vault.delete",
        str(doc.id),
    )
    await drain(env, {"vault.delete": env.delete_job().handle})
    assert doc.id not in env.store.documents and not env.storage.objects


# --------------------------------------------------------------------------- stalled ingestion
async def _stall_extracting(env: VaultEnv, doc: Document) -> None:
    env.extractor.fail_next(1)
    with pytest.raises(RuntimeError):
        await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.EXTRACTING


async def _stall_analysing(env: VaultEnv, doc: Document) -> None:
    def down(_: Any) -> dict[str, Any]:
        raise InfrastructureError("provider down")

    env.llm.script("InsightExtractor", down)
    with pytest.raises(Exception):  # noqa: B017
        await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.ANALYSING


@pytest.mark.parametrize("stall", [_stall_extracting, _stall_analysing])
@pytest.mark.parametrize("keep", [True, False])
async def test_stalled_ingestion_is_failed_and_purged_for_discard(stall: Any, keep: bool) -> None:
    env = VaultEnv()
    doc = await _quiet_upload(env, keep=keep)
    await stall(env, doc)
    env.clock.advance(59 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert env.doc(doc.id).status in {DocStatus.EXTRACTING, DocStatus.ANALYSING}  # not stalled long enough
    env.clock.advance(2 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    d = env.doc(doc.id)
    assert d.status is DocStatus.FAILED and d.failure_code == "ingest_stalled"
    rows = env.store.chunks.get(doc.id, [])
    # the sweeper has no storage port: it fails the row (and, for discard, redacts the chunk text in the same
    # commit); the bytes are removed by the purge step of the next vault.ingest run it schedules
    assert all((r.text is not None) is keep for r in rows)
    env.clock.advance(11 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    await drain(env, {"vault.ingest": env.ingest_uc().handle})
    assert await env.storage.exists(doc.storage_key) is keep
    assert len(env.audit_rows("document.original_discarded")) == (0 if keep else 1)
    assert all((r.text is not None) is keep for r in env.store.chunks.get(doc.id, []))
    await env.sweep().handle({}, last_attempt=False)  # nothing more to do, no second audit row
    await drain(env, {"vault.ingest": env.ingest_uc().handle})
    assert len(env.audit_rows("document.original_discarded")) == (0 if keep else 1)
    assert env.doc(doc.id).status is DocStatus.FAILED


async def test_stalled_documents_are_not_re_enqueued_before_the_stall_limit() -> None:
    env = VaultEnv()
    doc = await _quiet_upload(env)
    await _stall_extracting(env, doc)
    env.clock.advance(30 * MIN)
    await env.sweep().handle({}, last_attempt=False)
    assert not _jobs(env, "vault.ingest")
