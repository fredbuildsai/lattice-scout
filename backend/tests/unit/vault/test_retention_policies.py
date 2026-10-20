"""M2 vault, retention policies end to end (plan M2 acceptance; design M2-vault-ingestion.md §6 retention table, §10 invariant 2; ADR-009).

UploadDocument + IngestDocument + FakeTextExtractor + the real AgentsInsightExtractor over FakeLLM. Covers all
three deployment policies, `discard` deleting the bytes right after ready/failed/rejected, chunk text being gone,
quotes (<= 300 chars, from verified objections only) and summaries being the only retained content, and the single
`document.original_discarded` audit row.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.vault._env import (
    FAKE_QUOTE,
    OWNER,
    QUOTE,
    UNVERIFIED_MARK,
    VaultEnv,
    make_pages,
    pages_of,
    pdf_bytes,
)

from scout.shared import InfrastructureError
from scout.vault.domain.chunking import chunk_pages
from scout.vault.domain.enums import DocStatus, Retention, RetentionPolicy
from scout.vault.domain.insights import ItemStatus, Section, UnverifiedReason
from scout.vault.domain.retention import MAX_QUOTE
from scout.vault.domain.verification import norm

DATA = pdf_bytes(make_pages(3))
P = RetentionPolicy

MATRIX = [
    (P.KEEP, None, Retention.KEEP),
    (P.KEEP, False, Retention.KEEP),
    (P.KEEP, True, Retention.KEEP),
    (P.DISCARD, None, Retention.DISCARD),
    (P.DISCARD, False, Retention.DISCARD),
    (P.DISCARD, True, Retention.DISCARD),
    (P.USER_CHOICE, True, Retention.KEEP),
    (P.USER_CHOICE, False, Retention.DISCARD),
    (P.USER_CHOICE, None, Retention.DISCARD),
]


async def _run(policy: RetentionPolicy, keep: bool | None, data: bytes = DATA) -> tuple[VaultEnv, Any]:
    env = VaultEnv()
    res = await env.upload_uc(policy).upload(OWNER, "proposal.pdf", data, keep, None)
    await env.ingest(res.document)
    return env, res.document


@pytest.mark.parametrize(("policy", "keep", "retention"), MATRIX)
async def test_every_policy_and_choice_end_to_end(
    policy: RetentionPolicy, keep: bool | None, retention: Retention
) -> None:
    env, doc = await _run(policy, keep)
    d = env.doc(doc.id)
    rows = env.store.chunks[doc.id]
    assert d.retention is retention and d.status is DocStatus.READY
    if retention is Retention.KEEP:
        assert await env.storage.exists(doc.storage_key) and env.storage.objects[doc.storage_key] == DATA
        assert d.original_available and d.original_deleted_at is None
        assert all(r.text == r.chunk.text and r.text and r.redacted_at is None for r in rows)
        assert not env.audit_rows("document.original_discarded")
    else:
        assert not await env.storage.exists(doc.storage_key) and not env.storage.objects
        assert not d.original_available and d.original_deleted_at == env.clock.now()
        assert all(r.text is None and r.redacted_at is not None for r in rows)
        assert len(env.audit_rows("document.original_discarded")) == 1


# --------------------------------------------------------------------------- discard: what survives
async def test_discard_keeps_ids_offsets_pages_and_insights_but_not_text() -> None:
    env, doc = await _run(P.DISCARD, None)
    expected = chunk_pages(doc.id, pages_of(DATA))
    rows = env.store.chunks[doc.id]
    assert [
        (r.chunk.id, r.chunk.ord, r.chunk.page_from, r.chunk.page_to, r.chunk.char_start, r.chunk.char_end)
        for r in rows
    ] == [(c.id, c.ord, c.page_from, c.page_to, c.char_start, c.char_end) for c in expected]
    assert env.doc(doc.id).chunk_count == len(expected)
    ins = env.store.insights[doc.id]
    assert ins.visible()
    ids = {c.id for c in expected}
    assert all(i.chunk_ids and set(i.chunk_ids) <= ids for i in ins.visible())
    # the chunk list the repository serves has no text any more
    async with env.uow() as uow:
        assert [c.text for c in await uow.chunks.list(doc.id)] == [""] * len(expected)


async def test_discard_retains_only_verified_quotes_and_summaries() -> None:
    env, doc = await _run(P.DISCARD, None)
    rows = env.store.chunks[doc.id]
    verified_quotes = {
        norm(str(i.data["quote"]))
        for i in env.store.insights[doc.id].visible()
        if i.section is Section.REVIEWER_OBJECTIONS
    }
    assert verified_quotes == {norm(QUOTE)}
    quotes = [q for r in rows if r.retained for q in r.retained.quotes]
    assert [q.text for q in quotes] == [norm(QUOTE)] and not quotes[0].truncated
    assert all(len(q.text) <= MAX_QUOTE for q in quotes)
    holder = next(r for r in rows if QUOTE in r.chunk.text)
    assert holder.retained is not None and holder.retained.quotes[0].text == norm(QUOTE)
    texts = repr([(r.text, r.retained) for r in rows])
    assert FAKE_QUOTE not in texts and UNVERIFIED_MARK not in texts
    assert "lignin foam study design" not in texts  # nothing else of the document survived
    assert (
        rows[0].retained is not None
        and rows[0].retained.summary
        and "Aim from c0" in rows[0].retained.summary
    )
    assert all(len(r.retained.summary or "") <= 300 for r in rows if r.retained)


async def test_discard_never_keeps_more_than_300_characters_of_verbatim_text_anywhere() -> None:
    """A quote over 300 characters is not verified (so never shown or retained), and under discard no string in
    the stored insights, verified or not, exceeds 300 characters."""
    env = VaultEnv()

    def long_text(req: Any) -> dict[str, Any]:
        c = req.chunks[0]
        return {
            "kind": "declined_grant",
            "aims": [{"summary": c.text[:500], "chunk_ids": [c.chunk_id]}],
            "reviewer_objections": [{"quote": c.text[:600], "summary": "long", "chunk_ids": [c.chunk_id]}],
        }

    env.llm.script("InsightExtractor", long_text)
    doc = await env.upload(DATA, keep=False)
    await env.ingest(doc)
    stored = env.store.insights[doc.id]
    objection = next(i for i in stored.items if i.section is Section.REVIEWER_OBJECTIONS)
    assert objection.status is ItemStatus.UNVERIFIED and objection.reason is UnverifiedReason.QUOTE_TOO_LONG
    assert all(len(v) <= MAX_QUOTE for i in stored.items for v in i.data.values() if isinstance(v, str))
    assert all(not r.retained or not r.retained.quotes for r in env.store.chunks[doc.id])


async def test_keep_policy_keeps_the_long_unverified_text_it_was_given() -> None:
    """Only ``discard`` bounds stored text; the document itself is kept under ``keep``."""
    env = VaultEnv()

    def long_text(req: Any) -> dict[str, Any]:
        c = req.chunks[0]
        return {"kind": "other", "aims": [{"summary": c.text[:500], "chunk_ids": [c.chunk_id]}]}

    env.llm.script("InsightExtractor", long_text)
    doc = await env.upload(DATA, keep=True)
    await env.ingest(doc)
    assert any(len(str(i.data.get("summary", ""))) > MAX_QUOTE for i in env.store.insights[doc.id].items)


async def test_discard_with_nothing_verified_retains_no_quotes_or_summaries() -> None:
    env = VaultEnv()

    def nothing(req: Any) -> dict[str, Any]:
        return {
            "kind": "other",
            "aims": [{"summary": "uncited", "chunk_ids": []}],
            "reviewer_objections": [
                {"quote": FAKE_QUOTE, "summary": "x", "chunk_ids": [req.chunks[0].chunk_id]}
            ],
        }

    env.llm.script("InsightExtractor", nothing)
    doc = await env.upload(DATA, keep=False)
    await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.READY
    for r in env.store.chunks[doc.id]:
        assert r.text is None and (r.retained is None or (not r.retained.quotes and not r.retained.summary))


async def test_audit_row_reports_how_many_chunks_were_redacted() -> None:
    env, doc = await _run(P.DISCARD, None)
    (row,) = env.audit_rows("document.original_discarded")
    assert row.meta["document_id"] == str(doc.id)
    assert row.meta["chunks_redacted"] == len(env.store.chunks[doc.id]) == env.doc(doc.id).chunk_count
    assert row.actor_id in (None, OWNER)


# --------------------------------------------------------------------------- discard: every terminal outcome
@pytest.mark.parametrize("outcome", ["ready", "rejected", "failed"])
@pytest.mark.parametrize("keep", [True, False])
async def test_original_bytes_are_deleted_right_after_any_terminal_status_under_discard(
    outcome: str, keep: bool
) -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=keep)
    if outcome == "rejected":
        env.extractor.script_reject("encrypted")
    elif outcome == "failed":

        def down(_: Any) -> dict[str, Any]:
            raise InfrastructureError("provider down")

        env.llm.script("InsightExtractor", down)
    if outcome == "failed":
        with pytest.raises(Exception):  # noqa: B017
            await env.ingest(doc, last=True)
    else:
        await env.ingest(doc)
    d = env.doc(doc.id)
    assert d.status.value == outcome
    assert await env.storage.exists(doc.storage_key) is keep
    assert len(env.audit_rows("document.original_discarded")) == (0 if keep else 1)
    for r in env.store.chunks.get(doc.id, []):
        assert (r.text is not None) is keep
    if outcome == "rejected":
        assert len(env.audit_rows("document.rejected")) == 1
        assert not env.store.chunks.get(doc.id)
        if not keep:
            assert env.audit_rows("document.original_discarded")[0].meta["chunks_redacted"] == 0


async def test_bytes_exist_while_ingestion_runs_and_are_gone_when_it_ends() -> None:
    env = VaultEnv()
    doc = await env.upload(DATA, keep=False)
    seen: list[bool] = []
    env.extractor.hook = lambda: seen.append(doc.storage_key in env.storage.objects)
    await env.ingest(doc)
    assert seen == [True] and doc.storage_key not in env.storage.objects


async def test_keep_policy_ignores_the_users_untick_and_keeps_text_and_bytes() -> None:
    env, doc = await _run(P.KEEP, False)
    assert env.doc(doc.id).original_available
    assert [r.text for r in env.store.chunks[doc.id]] == [c.text for c in chunk_pages(doc.id, pages_of(DATA))]


async def test_discard_policy_ignores_the_users_tick() -> None:
    env, doc = await _run(P.DISCARD, True)
    assert not env.doc(doc.id).original_available and not env.storage.objects
