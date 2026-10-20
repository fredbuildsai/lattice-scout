"""M2 vault, deletion cascade (plan M2 acceptance "deleting a document removes the storage object, chunks and insights"; design M2-vault-ingestion.md §5 "DeleteDocument / vault.delete", §10 invariant 7).

Service delete (content gone at commit) + DeleteDocumentJob (object and row gone, retried on storage failure) +
recovery of a lost job by the sweeper. The `deleting` row is the outbox: it stays until the object is gone.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.vault._env import OWNER, VaultEnv, drain, make_pages, pdf_bytes

from scout.ports.jobs import JobStatus
from scout.shared import InfrastructureError
from scout.vault.domain.enums import DocStatus

DATA = pdf_bytes(make_pages(3))


async def _ready(env: VaultEnv, keep: bool = True) -> tuple[UUID, str]:
    doc = await env.upload(DATA, keep=keep)
    await env.ingest(doc)
    return doc.id, doc.storage_key


async def test_full_cascade_leaves_no_row_chunk_insight_or_object() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    assert doc_id not in env.store.chunks and doc_id not in env.store.insights
    assert await env.storage.exists(key) and doc_id in env.store.documents
    runs = await drain(env, {"vault.delete": env.delete_job().handle})
    assert runs == 1
    assert (
        doc_id not in env.store.documents
        and doc_id not in env.store.chunks
        and doc_id not in env.store.insights
    )
    assert not await env.storage.exists(key) and not env.storage.objects
    assert [j.status for j in env.jobs.jobs.values() if j.kind == "vault.delete"] == [JobStatus.SUCCEEDED]


async def test_job_removes_a_document_whose_object_is_already_gone() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env, keep=False)  # discard: bytes were purged at ingestion
    assert not await env.storage.exists(key)
    await env.service().delete(OWNER, doc_id)
    await env.delete_job().handle({"document_id": str(doc_id)}, last_attempt=False)
    assert doc_id not in env.store.documents


async def test_job_for_a_missing_document_is_skipped_as_gone() -> None:
    env = VaultEnv()
    out = await env.delete_job().handle({"document_id": str(UUID(int=31337))}, last_attempt=False)
    assert out == {"skipped": "gone"}


async def test_job_never_deletes_a_document_that_is_not_deleting() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    before = env.snapshot(doc_id)
    out = await env.delete_job().handle({"document_id": str(doc_id)}, last_attempt=False)
    assert "skipped" in out
    assert env.snapshot(doc_id) == before and await env.storage.exists(key)


async def test_job_is_idempotent() -> None:
    env = VaultEnv()
    doc_id, _ = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    job = env.delete_job()
    await job.handle({"document_id": str(doc_id)}, last_attempt=False)
    assert await job.handle({"document_id": str(doc_id)}, last_attempt=False) == {"skipped": "gone"}


async def test_storage_failure_reraises_and_leaves_the_row_as_outbox() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    env.storage.fail_next("delete")
    with pytest.raises(InfrastructureError):
        await env.delete_job().handle({"document_id": str(doc_id)}, last_attempt=False)
    assert env.doc(doc_id).status is DocStatus.DELETING and await env.storage.exists(key)
    assert doc_id not in env.store.chunks and doc_id not in env.store.insights


async def test_storage_failing_twice_then_succeeding_through_the_job_queue() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    env.storage.fail_next("delete", 2)
    runs = await drain(env, {"vault.delete": env.delete_job().handle})
    assert runs == 3
    assert doc_id not in env.store.documents and not await env.storage.exists(key)
    (job,) = [j for j in env.jobs.jobs.values() if j.kind == "vault.delete"]
    assert job.status is JobStatus.SUCCEEDED and job.attempts == 3 and job.last_error is not None


async def test_lost_delete_job_is_recovered_by_the_sweeper_after_two_storage_failures() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    env.jobs.fail_enqueue = True
    await env.service().delete(OWNER, doc_id)  # the enqueue is lost
    env.jobs.fail_enqueue = False
    assert not [j for j in env.jobs.jobs.values() if j.kind == "vault.delete"]
    env.clock.advance(timedelta(minutes=11))
    env.storage.fail_next("delete", 2)
    await env.sweep().handle({}, last_attempt=False)
    jobs = [j for j in env.jobs.jobs.values() if j.kind == "vault.delete"]
    assert len(jobs) == 1 and jobs[0].payload["document_id"] == str(doc_id)
    await drain(env, {"vault.delete": env.delete_job().handle})
    assert doc_id not in env.store.documents and not await env.storage.exists(key)


async def test_a_dead_delete_job_is_replaced_by_the_next_sweep_when_storage_recovers() -> None:
    env = VaultEnv()
    doc_id, key = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    env.storage.fail_next("delete", 1000)  # storage is down for every attempt of the first job
    await drain(env, {"vault.delete": env.delete_job().handle}, max_rounds=60)
    (dead,) = [j for j in env.jobs.jobs.values() if j.kind == "vault.delete"]
    assert dead.status is JobStatus.DEAD and doc_id in env.store.documents  # the row is still there
    env.storage.fail_next("delete", 0)  # storage is back
    env.clock.advance(timedelta(minutes=11))
    await env.sweep().handle({}, last_attempt=False)
    assert (
        len([j for j in env.jobs.jobs.values() if j.kind == "vault.delete" and j.status is JobStatus.QUEUED])
        == 1
    )
    await drain(env, {"vault.delete": env.delete_job().handle})
    assert doc_id not in env.store.documents and not await env.storage.exists(key)


async def test_deleting_an_in_progress_document_cleans_everything_too() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=False)
    env.extractor.fail_next(1)
    with pytest.raises(RuntimeError):
        await env.ingest(doc)  # now `extracting`
    await env.service().delete(OWNER, doc.id)
    await drain(env, {"vault.delete": env.delete_job().handle})
    assert doc.id not in env.store.documents and not env.storage.objects and doc.id not in env.store.chunks
