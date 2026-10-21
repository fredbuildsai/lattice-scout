"""ADR 016: without an authorized endpoint a document is read locally, kept, and analysed once one exists."""

from __future__ import annotations

from tests.unit.vault._env import VaultEnv, make_pages, pdf_bytes

from scout.vault.domain.enums import DocStatus

DATA = pdf_bytes(make_pages(3))


async def test_document_waits_for_an_approved_provider_and_nothing_is_sent() -> None:
    env = VaultEnv()
    env.rec.allowed = False
    doc = await env.upload(DATA, keep=True)
    result = await env.ingest(doc)
    d = env.doc(doc.id)
    assert result == {"status": "awaiting_approved_provider"} and d.status is DocStatus.AWAITING_PROVIDER
    assert env.extractor.calls == 1 and env.store.chunks[doc.id]  # parsing is local and happened
    assert env.rec.requests == [] and doc.id not in env.store.insights  # the LLM step was skipped
    assert d.failure_code is None and await env.storage.exists(d.storage_key)  # kept, not failed


async def test_running_the_job_again_stays_parked_and_does_not_extract_twice() -> None:
    env = VaultEnv()
    env.rec.allowed = False
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    await env.ingest(doc)
    assert env.extractor.calls == 1 and env.doc(doc.id).status is DocStatus.AWAITING_PROVIDER


async def test_sweep_requeues_parked_documents_only_when_a_provider_exists() -> None:
    env = VaultEnv()
    env.rec.allowed = False
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    before = len([j for j in env.jobs.jobs.values() if j.kind == "vault.ingest"])
    res = await env.sweep(provider_ready=lambda: env.rec.allowed).handle({}, last_attempt=False)
    assert res["awaiting_requeued"] == 0
    assert len([j for j in env.jobs.jobs.values() if j.kind == "vault.ingest"]) == before
    env.rec.allowed = True  # the owner authorized a provider
    res = await env.sweep(provider_ready=lambda: env.rec.allowed).handle({}, last_attempt=False)
    assert res["awaiting_requeued"] == 1
    await env.ingest(doc)  # what the requeued job does
    assert env.doc(doc.id).status is DocStatus.READY and env.store.insights[doc.id].visible()


async def test_a_discard_document_keeps_its_text_until_it_is_analysed() -> None:
    env = VaultEnv()
    env.rec.allowed = False
    doc = await env.upload(DATA, keep=False)
    await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.AWAITING_PROVIDER and await env.storage.exists(doc.storage_key)
    env.rec.allowed = True
    await env.ingest(doc)
    d = env.doc(doc.id)
    assert (
        d.status is DocStatus.READY and d.original_deleted_at is not None
    )  # retention applies after analysis


async def test_delete_works_while_parked() -> None:
    env = VaultEnv()
    env.rec.allowed = False
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    await env.service().delete(doc.owner_id, doc.id)
    assert env.doc(doc.id).status is DocStatus.DELETING
