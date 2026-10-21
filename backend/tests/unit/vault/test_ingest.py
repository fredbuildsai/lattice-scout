"""M2 vault, IngestDocument job handler (plan M2 test-first; design M2-vault-ingestion.md §5 "IngestDocument", §10 invariants 1, 2, 8).

Covers: state-driven resumability (crash at every commit, transient errors), rejected and failed outcomes,
last-attempt failure codes with purge, verification failures leaving the document ready, races with deletion.
The three retention policies end to end live in test_retention_policies.py.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from pydantic import JsonValue
from tests.unit.vault._env import (
    FAKE_QUOTE,
    OWNER,
    VaultEnv,
    make_pages,
    only_unverified,
    pages_of,
    pdf_bytes,
    scripted_insights,
)

from scout.ports.llm import LLMUnavailable
from scout.shared import InfrastructureError
from scout.vault.domain.chunking import chunk_pages
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocStatus, PageBasis, Retention
from scout.vault.ports.extraction import RejectCode

DATA = pdf_bytes(make_pages(3))


async def _uploaded(env: VaultEnv, keep: bool | None = True, data: bytes = DATA) -> Document:
    return await env.upload(data, keep=keep)


# --------------------------------------------------------------------------- happy path and skips
async def test_missing_document_is_skipped_as_gone() -> None:
    env = VaultEnv()
    payload: dict[str, JsonValue] = {
        "account_id": str(OWNER),
        "session_id": None,
        "feature": "vault.ingest",
        "document_id": str(UUID(int=5)),
    }
    assert await env.ingest_uc().handle(payload, last_attempt=False) == {"skipped": "gone"}


async def test_deleting_document_is_skipped_as_gone() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    await env.service().delete(OWNER, doc.id)
    assert await env.ingest(doc) == {"skipped": "gone"}
    assert env.extractor.calls == 0 and not env.rec.requests


async def test_keep_document_reaches_ready_with_chunks_and_insights() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    await env.ingest(doc)
    d = env.doc(doc.id)
    expected = chunk_pages(doc.id, pages_of(DATA))
    rows = env.store.chunks[doc.id]
    assert d.status is DocStatus.READY and d.failure_code is None
    assert d.pages == 3 and d.page_basis is PageBasis.PHYSICAL and d.extractor == "fake@1"
    assert d.chunk_count == len(expected) and [r.chunk for r in rows] == expected
    assert all(r.text == r.chunk.text and r.text for r in rows)
    assert d.extracted_at is not None and d.ready_at == env.clock.now()
    ins = env.store.insights[doc.id]
    assert ins.document_id == doc.id and ins.visible() and len(ins.items) > len(ins.visible())
    assert d.kind_suggested is DocKind.DECLINED_GRANT and d.kind_reason == "Reviewer comments present"
    assert d.kind is None  # the user's own choice is never overwritten by the suggestion
    assert await env.storage.exists(d.storage_key)


async def test_user_kind_survives_the_suggestion() -> None:
    env = VaultEnv()
    doc = (await env.upload_uc().upload(OWNER, "a.pdf", DATA, True, DocKind.LAB_NOTES)).document
    await env.ingest(doc)
    d = env.doc(doc.id)
    assert d.kind is DocKind.LAB_NOTES and d.kind_suggested is DocKind.DECLINED_GRANT
    assert d.effective_kind is DocKind.LAB_NOTES


async def test_visible_items_cite_chunks_of_the_document() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    await env.ingest(doc)
    ids = {r.chunk.id for r in env.store.chunks[doc.id]}
    for item in env.store.insights[doc.id].visible():
        assert item.chunk_ids and set(item.chunk_ids) <= ids


async def test_running_a_finished_job_again_is_a_no_op() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    await env.ingest(doc)
    before = env.snapshot(doc.id)
    calls, llm_calls = env.extractor.calls, len(env.rec.requests)
    assert await env.ingest(doc) == {"skipped": "done"}
    assert env.snapshot(doc.id) == before
    assert env.extractor.calls == calls and len(env.rec.requests) == llm_calls


async def test_last_attempt_flag_does_not_change_a_successful_run() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    await env.ingest(doc, last=True)
    assert env.doc(doc.id).status is DocStatus.READY


# --------------------------------------------------------------------------- rejected and missing
@pytest.mark.parametrize(
    "code",
    [
        "encrypted",
        "no_text_layer",
        "too_many_pages",
        "text_too_long",
        "corrupt_file",
        "zip_bomb",
        "extraction_timeout",
        "too_complex",
    ],
)
async def test_rejected_files_are_marked_rejected_and_never_retried(code: RejectCode) -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    env.extractor.script_reject(code)
    await env.ingest(doc)  # permanent errors do not raise: the job succeeds
    d = env.doc(doc.id)
    assert d.status is DocStatus.REJECTED and d.failure_code == code
    assert len(env.audit_rows("document.rejected")) == 1
    assert doc.id not in env.store.insights and not env.rec.requests
    assert await env.ingest(doc) == {"skipped": "done"} and env.extractor.calls == 1


async def test_rejected_keep_document_keeps_its_bytes_and_no_discard_audit() -> None:
    env = VaultEnv()
    doc = await _uploaded(env, keep=True)
    env.extractor.script_reject("encrypted")
    await env.ingest(doc)
    assert await env.storage.exists(doc.storage_key)
    assert not env.audit_rows("document.original_discarded")


async def test_missing_original_fails_the_document_permanently() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    env.storage.objects.clear()
    await env.ingest(doc)
    d = env.doc(doc.id)
    assert d.status is DocStatus.FAILED and d.failure_code == "original_missing"
    assert env.extractor.calls == 0 and not env.rec.requests


# --------------------------------------------------------------------------- verification failures
async def test_all_items_unverified_still_leaves_the_document_ready() -> None:
    env = VaultEnv()
    env.llm.script("InsightExtractor", only_unverified)
    doc = await _uploaded(env)
    await env.ingest(doc)
    d = env.doc(doc.id)
    ins = env.store.insights[doc.id]
    assert d.status is DocStatus.READY and d.kind_suggested is DocKind.PROPOSAL
    assert len(ins.items) == 3 and ins.visible() == []
    assert {i.data.get("quote") for i in ins.items if "quote" in i.data} == {FAKE_QUOTE}


# --------------------------------------------------------------------------- resume
async def test_extractor_crash_is_reraised_then_the_rerun_completes() -> None:
    env, control = VaultEnv(), VaultEnv()
    doc, cdoc = await _uploaded(env), await _uploaded(control)
    await control.ingest(cdoc)
    env.extractor.fail_next(1)
    with pytest.raises(Exception, match="extractor crashed"):
        await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.EXTRACTING
    await env.ingest(doc)
    assert env.snapshot(doc.id) == control.snapshot(cdoc.id)


async def test_storage_get_failure_is_reraised_then_the_rerun_completes() -> None:
    env, control = VaultEnv(), VaultEnv()
    doc, cdoc = await _uploaded(env), await _uploaded(control)
    await control.ingest(cdoc)
    env.storage.fail_next("get")
    with pytest.raises(InfrastructureError):
        await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.EXTRACTING
    await env.ingest(doc)
    assert env.snapshot(doc.id) == control.snapshot(cdoc.id)


async def test_llm_outage_keeps_the_chunks_and_the_rerun_does_not_extract_again() -> None:
    env, control = VaultEnv(), VaultEnv()
    doc, cdoc = await _uploaded(env), await _uploaded(control)
    await control.ingest(cdoc)

    def down(_: Any) -> dict[str, Any]:
        raise InfrastructureError("provider down")

    env.llm.script("InsightExtractor", down)
    with pytest.raises(LLMUnavailable):
        await env.ingest(doc)
    d = env.doc(doc.id)
    assert d.status is DocStatus.ANALYSING and env.store.chunks[doc.id] and doc.id not in env.store.insights
    env.llm.script("InsightExtractor", scripted_insights)
    await env.ingest(doc)
    assert env.extractor.calls == 1
    assert env.snapshot(doc.id) == control.snapshot(cdoc.id)


@pytest.mark.parametrize("keep", [True, False])
async def test_crash_at_every_commit_then_rerun_gives_identical_state(keep: bool) -> None:
    control = VaultEnv()
    cdoc = await _uploaded(control, keep=keep)
    control.faults.arm(None)
    await control.ingest(cdoc)
    commits = control.faults.commits
    assert commits >= 3  # start, extraction, analysis (+ purge audit for discard)
    expected = control.snapshot(cdoc.id)
    for k in range(1, commits + 1):
        env = VaultEnv()
        doc = await _uploaded(env, keep=keep)
        env.faults.arm(k)
        failures = 0
        for _ in range(commits + 3):
            try:
                await env.ingest(doc)
            except RuntimeError:
                failures += 1
            else:
                break
        assert failures == 1, f"commit {k} should have failed exactly once"
        assert env.snapshot(doc.id) == expected, f"state differs after crash at commit {k}"
        assert len(env.audit_rows("document.original_discarded")) == (0 if keep else 1)
        assert len(env.audit_rows("document.rejected")) == 0
        assert await env.ingest(doc) == {"skipped": "done"}
        assert len(env.audit_rows("document.original_discarded")) == (0 if keep else 1)


async def test_crash_while_deleting_the_original_resumes_and_audits_once() -> None:
    env = VaultEnv()
    doc = await _uploaded(env, keep=False)
    env.storage.fail_next("delete")
    with pytest.raises(InfrastructureError):
        await env.ingest(doc)
    d = env.doc(doc.id)
    assert d.status is DocStatus.READY and d.original_deleted_at is None
    assert await env.storage.exists(doc.storage_key)
    assert all(r.text is None for r in env.store.chunks[doc.id])  # text is already gone from the database
    await env.ingest(doc)
    assert not await env.storage.exists(doc.storage_key)
    assert len(env.audit_rows("document.original_discarded")) == 1
    assert env.doc(doc.id).original_deleted_at == env.clock.now()
    assert env.extractor.calls == 1


# --------------------------------------------------------------------------- last attempt
def _break(env: VaultEnv, how: str) -> None:
    if how == "llm":

        def down(_: Any) -> dict[str, Any]:
            raise InfrastructureError("provider down")

        env.llm.script("InsightExtractor", down)
    elif how == "extractor":
        env.extractor.fail_next(1)
    else:
        env.storage.fail_next("get")


@pytest.mark.parametrize(
    ("how", "code"),
    [("llm", "insights_unavailable"), ("extractor", "extraction_failed"), ("storage", "storage_unavailable")],
)
@pytest.mark.parametrize("keep", [True, False])
async def test_last_attempt_failure_marks_failed_and_purges_for_discard(
    how: str, code: str, keep: bool
) -> None:
    env = VaultEnv()
    doc = await _uploaded(env, keep=keep)
    _break(env, how)
    with pytest.raises(Exception):  # noqa: B017 - the worker still sees the error
        await env.ingest(doc, last=True)
    d = env.doc(doc.id)
    assert d.status is DocStatus.FAILED and d.failure_code == code
    rows = env.store.chunks.get(doc.id, [])
    if keep:
        assert await env.storage.exists(doc.storage_key) and not env.audit_rows("document.original_discarded")
        assert all(r.text for r in rows)
    else:
        assert not await env.storage.exists(doc.storage_key)
        assert len(env.audit_rows("document.original_discarded")) == 1
        assert d.original_deleted_at == env.clock.now()
        assert all(r.text is None for r in rows)  # invariant 2: discard + terminal => no chunk text
    assert await env.ingest(doc) == {"skipped": "done"}


async def test_not_last_attempt_failure_leaves_the_document_in_progress() -> None:
    env = VaultEnv()
    doc = await _uploaded(env, keep=False)
    env.extractor.fail_next(1)
    with pytest.raises(Exception, match="extractor crashed"):
        await env.ingest(doc, last=False)
    assert env.doc(doc.id).status is DocStatus.EXTRACTING
    assert await env.storage.exists(doc.storage_key) and not env.audit_rows("document.original_discarded")


# --------------------------------------------------------------------------- races with deletion
def _delete_behind_the_back(env: VaultEnv, doc_id: UUID) -> None:
    d = env.store.documents[doc_id]
    d.request_deletion(env.clock.now())
    env.store.chunks.pop(doc_id, None)
    env.store.insights.pop(doc_id, None)


async def test_deletion_during_extraction_is_not_revived() -> None:
    env = VaultEnv()
    doc = await _uploaded(env)
    env.extractor.hook = lambda: _delete_behind_the_back(env, doc.id)
    await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.DELETING
    assert doc.id not in env.store.chunks and doc.id not in env.store.insights


async def test_deletion_during_analysis_is_not_revived() -> None:
    env = VaultEnv()
    doc = await _uploaded(env, keep=False)

    def delete_then_answer(req: Any) -> dict[str, Any]:
        _delete_behind_the_back(env, doc.id)
        return scripted_insights(req)

    env.llm.script("InsightExtractor", delete_then_answer)
    await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.DELETING
    assert doc.id not in env.store.chunks and doc.id not in env.store.insights
    assert not env.audit_rows("document.original_discarded")  # the delete job owns the bytes now
    assert env.doc(doc.id).retention is Retention.DISCARD
