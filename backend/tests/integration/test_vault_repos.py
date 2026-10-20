"""Vault SQL repositories against PostgreSQL (migration 0004)."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from scout.adapters.persistence.db import Database
from scout.ports.audit import AuditEntry
from scout.shared import AccountId
from scout.vault.adapters.sql import SqlVaultUnitOfWork
from scout.vault.domain.chunking import ChunkParams, chunk_pages
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocumentMime, Retention
from scout.vault.domain.insights import DocumentInsights, Item, Objection, Stage, StoredInsights
from scout.vault.domain.retention import retained_content
from scout.vault.domain.verification import verify
from scout.vault.ports.repos import DuplicateDocument

pytestmark = pytest.mark.integration
NOW = datetime(2026, 1, 1, tzinfo=UTC)
A, B = AccountId(UUID(int=101)), AccountId(UUID(int=102))
QUOTE = "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501"


async def _accounts(db: Database) -> None:
    async with db.engine.begin() as conn:
        for n, acc in enumerate((A, B)):
            await conn.execute(
                text(
                    "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                    " created_at, updated_at) VALUES (:id, :e, false, false, 'f', 'user', now(), now())"
                ),
                {"id": acc, "e": f"u{n}@example.edu"},
            )


def _doc(
    n: int, owner: AccountId = A, sha: str | None = None, retention: Retention = Retention.KEEP
) -> Document:
    return Document.upload(
        id_=UUID(int=n), owner_id=owner, filename=f"d{n}.pdf", mime=DocumentMime.PDF, size_bytes=10,
        sha256=sha or f"{n:064x}", storage_key=f"vault/{owner}/{UUID(int=n)}", retention=retention,
        kind_hint=None, now=NOW,
    )  # fmt: skip


async def test_document_round_trip_owner_isolation_and_unique_hash(db: Database) -> None:
    await _accounts(db)
    async with SqlVaultUnitOfWork(db) as uow:
        d = _doc(1)
        await uow.documents.add(d)
        await uow.commit()
    async with SqlVaultUnitOfWork(db) as uow:
        got = await uow.documents.get(d.id, A)
        assert got is not None and got.filename == "d1.pdf" and got.sha256 == d.sha256
        assert await uow.documents.get(d.id, B) is None  # another owner: nothing
        assert await uow.documents.count_active(A) == 1 and await uow.documents.count_active(B) == 0
        assert (await uow.documents.find_active_by_sha(A, d.sha256)) is not None
        assert [x.id for x in await uow.documents.list_for_owner(A)] == [d.id]
    async with SqlVaultUnitOfWork(db) as uow:
        with pytest.raises(DuplicateDocument):
            await uow.documents.add(_doc(2, sha=d.sha256))
        await uow.documents.add(_doc(3, owner=B, sha=d.sha256))  # same bytes, other owner: fine
        await uow.commit()
    async with SqlVaultUnitOfWork(db) as uow:  # a failed document does not block re-upload
        failed = await uow.documents.get_for_update(d.id)
        assert failed is not None
        failed.fail("x", NOW)
        await uow.documents.save(failed)
        await uow.documents.add(_doc(4, sha=d.sha256))
        await uow.commit()


async def test_deleting_documents_are_invisible_but_stale_sweep_finds_them(db: Database) -> None:
    await _accounts(db)
    old = NOW - timedelta(hours=2)
    async with SqlVaultUnitOfWork(db) as uow:
        up, dele, disc, stuck = _doc(1), _doc(2), _doc(3, retention=Retention.DISCARD), _doc(4)
        dele.request_deletion(old)
        disc.start_extraction(old)
        disc.fail("x", old)
        stuck.start_extraction(old)
        up.updated_at = old
        for d in (up, dele, disc, stuck):
            await uow.documents.add(d)
        await uow.commit()
    async with SqlVaultUnitOfWork(db) as uow:
        assert await uow.documents.get(dele.id, A) is None and len(await uow.documents.list_for_owner(A)) == 3
        stale = await uow.documents.list_stale(NOW - timedelta(minutes=10), NOW - timedelta(hours=1), 100)
        assert stale.uploaded == (up.id,) and stale.deleting == (dele.id,)
        assert stale.purge == (disc.id,) and stale.stalled == (stuck.id,)


async def test_chunks_redact_and_insights_round_trip_then_cascade(db: Database) -> None:
    await _accounts(db)
    doc = _doc(1, retention=Retention.DISCARD)
    pages = ["Aims. " * 30, f"Reviewer 2: {QUOTE} at the proposed bio-content.", "Budget. " * 30]
    chunks = chunk_pages(doc.id, pages, ChunkParams(target=300, max=400, min=50))
    by_label = {c.label: c for c in chunks}
    label = next(c.label for c in chunks if QUOTE in c.text)
    items = verify(
        DocumentInsights(
            aims=[Item(summary="Make foams", chunk_ids=[chunks[0].label])],
            reviewer_objections=[Objection(quote=QUOTE, summary="fire", chunk_ids=[label])],
        ),
        by_label, Stage.FULL,
    )  # fmt: skip
    stored = StoredInsights(
        document_id=doc.id, items=items, keywords=["lignin"], title="T", kind_suggested=DocKind.DECLINED_GRANT,
        kind_reason=None, prompt_version="InsightExtractor@1.0.0", model="fake", mode="full", slices=1, cost_usd=0.00123,
    )  # fmt: skip
    async with SqlVaultUnitOfWork(db) as uow:
        await uow.documents.add(doc)
        await uow.chunks.replace_all(doc.id, chunks)
        await uow.chunks.replace_all(doc.id, chunks)  # idempotent
        await uow.insights.upsert(stored)
        await uow.insights.upsert(stored)
        await uow.audit.add(AuditEntry(id=UUID(int=900), actor_id=A, action="document.uploaded", at=NOW))
        await uow.commit()
    async with SqlVaultUnitOfWork(db) as uow:
        assert [c.id for c in await uow.chunks.list(doc.id)] == [c.id for c in chunks]
        back = await uow.insights.get(doc.id)
        assert back is not None and back.items == items and back.visible() == items
        assert back.cost_usd == pytest.approx(0.00123) and back.kind_suggested is DocKind.DECLINED_GRANT
        n = await uow.chunks.redact(doc.id, retained_content(chunks, items), NOW)
        await uow.commit()
        assert n == len(chunks)
    async with SqlVaultUnitOfWork(db) as uow:
        assert all(c.text == "" for c in await uow.chunks.list(doc.id))
        kept = await uow.chunks.retained(doc.id)
        quotes = [q.text for r in kept.values() for q in r.quotes]
        assert quotes == [QUOTE] and all(len(q) <= 300 for q in quotes)
    async with db.engine.connect() as conn:
        nulls = (
            await conn.execute(text("SELECT count(*) FROM document_chunk WHERE text IS NOT NULL"))
        ).scalar()
        assert nulls == 0  # the original text is gone from the table, not just hidden
    async with SqlVaultUnitOfWork(db) as uow:  # hard delete cascades
        assert await uow.chunks.delete_all(doc.id) == len(chunks)
        await uow.insights.delete(doc.id)
        await uow.documents.hard_delete(doc.id)
        await uow.commit()
    async with db.engine.connect() as conn:
        for t in ("document", "document_chunk", "document_insights"):
            count = (await conn.execute(text(f"SELECT count(*) FROM {t}"))).scalar()  # noqa: S608
            assert count == 0
