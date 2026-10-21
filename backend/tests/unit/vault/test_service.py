"""M2 vault, VaultService: list/get/set_kind/delete (plan M2 test-first; design M2-vault-ingestion.md §5 "SetDocumentKind/DeleteDocument", §10 invariants 6 and 7).

Covers owner isolation (404 and nothing changes), `deleting` documents being invisible, kind setting without audit,
and the delete cascade at commit (chunks, insights, audit row, `vault.delete` job; enqueue failure ignored).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from tests.unit.vault._env import OTHER, OWNER, VaultEnv, make_pages, pdf_bytes

from scout.ports.jobs import Job, JobStatus
from scout.shared import AccountId, NotFound
from scout.vault.domain.enums import DocKind, DocStatus

DATA = pdf_bytes(make_pages(3))


async def _ready(env: VaultEnv, *, keep: bool = True, data: bytes = DATA, owner: AccountId = OWNER) -> UUID:
    doc = await env.upload(data, keep=keep, owner=owner)
    await env.ingest(doc)
    return doc.id


def _delete_jobs(env: VaultEnv) -> list[Job]:
    return [j for j in env.jobs.jobs.values() if j.kind == "vault.delete"]


# --------------------------------------------------------------------------- list / get
async def test_list_is_owner_scoped_newest_first_without_deleting() -> None:
    env = VaultEnv()
    a = (await env.upload(pdf_bytes(make_pages(1, seed=1)))).id
    env.clock.advance(timedelta(minutes=1))
    b = (await env.upload(pdf_bytes(make_pages(1, seed=2)))).id
    env.clock.advance(timedelta(minutes=1))
    c = (await env.upload(pdf_bytes(make_pages(1, seed=3)))).id
    await env.upload(pdf_bytes(make_pages(1, seed=4)), owner=OTHER)
    svc = env.service()
    assert [d.id for d in await svc.list(OWNER)] == [c, b, a]
    await svc.delete(OWNER, b)
    assert [d.id for d in await svc.list(OWNER)] == [c, a]
    assert len(await svc.list(OTHER)) == 1


async def test_get_returns_document_and_no_insights_before_ingestion() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA)
    got, insights = await env.service().get(OWNER, doc.id)
    assert got.id == doc.id and got.status is DocStatus.UPLOADED and insights is None


async def test_get_returns_stored_insights_after_ingestion() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    got, insights = await env.service().get(OWNER, doc_id)
    assert got.status is DocStatus.READY
    assert insights is not None and insights.document_id == doc_id and insights.visible()


@pytest.mark.parametrize("op", ["get", "set_kind", "delete"])
async def test_other_owners_document_is_not_found_and_untouched(op: str) -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    before = env.snapshot(doc_id)
    commits, audit, jobs = env.store.commits, len(env.store.audit.entries), len(env.jobs.jobs)
    svc = env.service()
    with pytest.raises(NotFound):
        if op == "get":
            await svc.get(OTHER, doc_id)
        elif op == "set_kind":
            await svc.set_kind(OTHER, doc_id, DocKind.LAB_NOTES)
        else:
            await svc.delete(OTHER, doc_id)
    assert env.snapshot(doc_id) == before and env.doc(doc_id).kind is None
    assert len(env.store.audit.entries) == audit and len(env.jobs.jobs) == jobs
    assert env.store.commits == commits


@pytest.mark.parametrize("op", ["get", "set_kind", "delete"])
async def test_unknown_document_is_not_found(op: str) -> None:
    svc = VaultEnv().service()
    ghost = UUID(int=424242)
    with pytest.raises(NotFound):
        if op == "get":
            await svc.get(OWNER, ghost)
        elif op == "set_kind":
            await svc.set_kind(OWNER, ghost, DocKind.OTHER)
        else:
            await svc.delete(OWNER, ghost)


# --------------------------------------------------------------------------- set_kind
async def test_set_kind_persists_returns_document_and_writes_no_audit() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    audit = list(env.audit_actions())
    env.clock.advance(timedelta(minutes=5))
    out = await env.service().set_kind(OWNER, doc_id, DocKind.FUNDED_GRANT)
    d = env.doc(doc_id)
    assert out.id == doc_id and out.kind is DocKind.FUNDED_GRANT and d.kind is DocKind.FUNDED_GRANT
    assert d.effective_kind is DocKind.FUNDED_GRANT and d.kind_suggested is DocKind.DECLINED_GRANT
    assert d.updated_at == env.clock.now()
    assert env.audit_actions() == audit
    assert d.status is DocStatus.READY


async def test_set_kind_is_allowed_before_ingestion_and_for_terminal_documents() -> None:
    env = VaultEnv()
    fresh = await env.upload(DATA)
    out = await env.service().set_kind(OWNER, fresh.id, DocKind.PROPOSAL)
    assert out.kind is DocKind.PROPOSAL and out.status is DocStatus.UPLOADED
    env.extractor.script_reject("encrypted")
    rejected = await env.upload(pdf_bytes(make_pages(1, seed=9)))
    await env.ingest(rejected)
    assert (await env.service().set_kind(OWNER, rejected.id, DocKind.OTHER)).kind is DocKind.OTHER


# --------------------------------------------------------------------------- delete
async def test_delete_removes_content_at_commit_and_keeps_a_deleting_row() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    assert env.store.chunks[doc_id] and doc_id in env.store.insights
    await env.service().delete(OWNER, doc_id)
    d = env.doc(doc_id)
    assert d.status is DocStatus.DELETING and d.deleted_at == env.clock.now()
    assert doc_id not in env.store.chunks and doc_id not in env.store.insights
    assert await env.storage.exists(d.storage_key)  # the object goes with the vault.delete job


async def test_delete_audits_and_enqueues_the_storage_job() -> None:
    env = VaultEnv()
    doc_id = await _ready(env, keep=True)
    await env.service().delete(OWNER, doc_id)
    (row,) = env.audit_rows("document.deleted")
    assert row.actor_id == OWNER
    assert row.meta["document_id"] == str(doc_id) and row.meta["retention"] == "keep"
    assert row.meta["original_present"] is True
    (job,) = _delete_jobs(env)
    assert job.status is JobStatus.QUEUED and job.payload["document_id"] == str(doc_id)
    assert env.jobs.dedupe[job.id] == ("vault.delete", str(doc_id))


async def test_delete_of_a_discarded_document_reports_no_original() -> None:
    env = VaultEnv()
    doc_id = await _ready(env, keep=False)
    await env.service().delete(OWNER, doc_id)
    (row,) = env.audit_rows("document.deleted")
    assert row.meta["retention"] == "discard" and row.meta["original_present"] is False


async def test_delete_before_ingestion_reports_the_original_as_present() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=False)
    await env.service().delete(OWNER, doc.id)
    assert env.audit_rows("document.deleted")[0].meta["original_present"] is True
    assert await env.ingest(doc) == {"skipped": "gone"}


async def test_deleting_documents_disappear_from_get_set_kind_and_a_second_delete() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    svc = env.service()
    await svc.delete(OWNER, doc_id)
    with pytest.raises(NotFound):
        await svc.get(OWNER, doc_id)
    with pytest.raises(NotFound):
        await svc.set_kind(OWNER, doc_id, DocKind.OTHER)
    with pytest.raises(NotFound):
        await svc.delete(OWNER, doc_id)
    assert len(env.audit_rows("document.deleted")) == 1 and len(_delete_jobs(env)) == 1


async def test_delete_succeeds_when_the_enqueue_fails() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    env.jobs.fail_enqueue = True
    await env.service().delete(OWNER, doc_id)  # no exception: the sweeper recovers the deleting row
    assert env.doc(doc_id).status is DocStatus.DELETING and not _delete_jobs(env)
    assert doc_id not in env.store.chunks and doc_id not in env.store.insights


async def test_delete_frees_the_hash_for_a_new_upload() -> None:
    env = VaultEnv()
    doc_id = await _ready(env)
    await env.service().delete(OWNER, doc_id)
    again = await env.upload_uc().upload(OWNER, "again.pdf", DATA, True, None)
    assert again.created is True and again.document.id != doc_id
