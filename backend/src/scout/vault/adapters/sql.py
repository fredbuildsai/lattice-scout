"""SQL repositories for the vault context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.audit import SqlAuditLog
from scout.adapters.persistence.models import audit_log
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.ports.audit import AuditLog
from scout.shared import AccountId
from scout.vault.domain.chunking import Chunk
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocStatus, DocumentMime, PageBasis, Retention
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import (
    InsightItem,
    ItemStatus,
    Section,
    Stage,
    StoredInsights,
    UnverifiedReason,
)
from scout.vault.domain.retention import Quote, Retained
from scout.vault.ports.repos import DuplicateDocument, StaleSets

_TERMINAL = ("ready", "failed", "rejected")


def _doc(row: Any) -> Document:
    r = row._mapping
    return Document(
        id=r["id"], owner_id=AccountId(r["owner_id"]), filename=r["filename"], mime=DocumentMime(r["mime"]),
        size_bytes=r["size_bytes"], sha256=r["sha256"].strip(), storage_key=r["storage_key"],
        retention=Retention(r["retention"]), created_at=r["created_at"], updated_at=r["updated_at"],
        status=DocStatus(r["status"]), kind=DocKind(r["kind"]) if r["kind"] else None,
        kind_suggested=DocKind(r["kind_suggested"]) if r["kind_suggested"] else None, kind_reason=r["kind_reason"],
        failure_code=r["failure_code"], pages=r["pages"],
        page_basis=PageBasis(r["page_basis"]) if r["page_basis"] else None, chunk_count=r["chunk_count"],
        extractor=r["extractor"], original_deleted_at=r["original_deleted_at"], extracted_at=r["extracted_at"],
        ready_at=r["ready_at"], deleted_at=r["deleted_at"],
    )  # fmt: skip


def _values(d: Document) -> dict[str, Any]:
    return {
        "owner_id": d.owner_id, "filename": d.filename, "mime": d.mime.value, "size_bytes": d.size_bytes,
        "sha256": d.sha256, "storage_key": d.storage_key, "retention": d.retention.value,
        "kind": d.kind.value if d.kind else None,
        "kind_suggested": d.kind_suggested.value if d.kind_suggested else None, "kind_reason": d.kind_reason,
        "status": d.status.value, "failure_code": d.failure_code, "pages": d.pages,
        "page_basis": d.page_basis.value if d.page_basis else None, "chunk_count": d.chunk_count,
        "extractor": d.extractor, "original_deleted_at": d.original_deleted_at, "extracted_at": d.extracted_at,
        "ready_at": d.ready_at, "deleted_at": d.deleted_at, "created_at": d.created_at, "updated_at": d.updated_at,
    }  # fmt: skip


class SqlDocumentRepo:
    """Implements ``DocumentRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, document_id: UUID, owner_id: AccountId) -> Document | None:
        """Owner's document (never a ``deleting`` one)."""
        t = m.document
        row = (
            await self._s.execute(
                select(t).where(t.c.id == document_id, t.c.owner_id == owner_id, t.c.status != "deleting")
            )
        ).first()
        return _doc(row) if row else None

    async def get_for_update(self, document_id: UUID) -> Document | None:
        """Lock the row for the rest of the transaction."""
        row = (
            await self._s.execute(select(m.document).where(m.document.c.id == document_id).with_for_update())
        ).first()
        return _doc(row) if row else None

    async def list_for_owner(self, owner_id: AccountId) -> list[Document]:
        """Newest first."""
        t = m.document
        rows = await self._s.execute(
            select(t)
            .where(t.c.owner_id == owner_id, t.c.status != "deleting")
            .order_by(t.c.created_at.desc(), t.c.id)
        )
        return [_doc(r) for r in rows]

    async def find_active_by_sha(self, owner_id: AccountId, sha256: str) -> Document | None:
        """Same bytes already in the vault."""
        t = m.document
        row = (
            await self._s.execute(
                select(t).where(
                    t.c.owner_id == owner_id,
                    t.c.sha256 == sha256,
                    t.c.status.notin_(("failed", "rejected", "deleting")),
                )
            )
        ).first()
        return _doc(row) if row else None

    async def count_active(self, owner_id: AccountId) -> int:
        """Documents counting against the limit."""
        t = m.document
        n = await self._s.scalar(
            select(func.count()).select_from(t).where(t.c.owner_id == owner_id, t.c.status != "deleting")
        )
        return int(n or 0)

    async def count_uploaded_since(self, owner_id: AccountId, since: datetime) -> int:
        """Count ``document.uploaded`` audit rows, so upload-delete loops do not reset the limit."""
        a = audit_log
        n = await self._s.scalar(
            select(func.count())
            .select_from(a)
            .where(a.c.actor_id == owner_id, a.c.action == "document.uploaded", a.c.at >= since)
        )
        return int(n or 0)

    async def add(self, document: Document) -> None:
        """Insert; a lost race on the unique index becomes ``DuplicateDocument``."""
        try:
            async with self._s.begin_nested():
                await self._s.execute(insert(m.document).values(id=document.id, **_values(document)))
        except IntegrityError as exc:
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                != "document_owner_sha_active_idx"
            ):
                raise  # e.g. the account was deleted meanwhile: not a duplicate
            raise DuplicateDocument("Document already in the vault") from exc

    async def save(self, document: Document) -> None:
        """Update."""
        await self._s.execute(
            update(m.document).where(m.document.c.id == document.id).values(**_values(document))
        )

    async def hard_delete(self, document_id: UUID) -> None:
        """Remove the row (children cascade)."""
        await self._s.execute(delete(m.document).where(m.document.c.id == document_id))

    async def list_stale(self, older_than: datetime, stalled_before: datetime, limit: int) -> StaleSets:
        """Rows the sweeper acts on."""
        t = m.document

        async def ids(*where: Any) -> tuple[UUID, ...]:
            rows = await self._s.execute(select(t.c.id).where(*where).order_by(t.c.created_at).limit(limit))
            return tuple(r[0] for r in rows)

        return StaleSets(
            uploaded=await ids(t.c.status == "uploaded", t.c.updated_at < older_than),
            purge=await ids(
                t.c.retention == "discard",
                t.c.status.in_(_TERMINAL),
                t.c.original_deleted_at.is_(None),
                t.c.updated_at < older_than,
            ),  # fmt: skip
            deleting=await ids(t.c.status == "deleting", t.c.updated_at < older_than),
            stalled=await ids(t.c.status.in_(("extracting", "analysing")), t.c.updated_at < stalled_before),
        )


class SqlChunkRepo:
    """Implements ``ChunkRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def replace_all(self, document_id: UUID, chunks: Sequence[Chunk]) -> None:
        """Delete and insert (a re-run of extraction gives identical rows)."""
        await self._s.execute(delete(m.document_chunk).where(m.document_chunk.c.document_id == document_id))
        if chunks:
            await self._s.execute(
                insert(m.document_chunk),
                [
                    {
                        "id": c.id, "document_id": document_id, "ord": c.ord, "label": c.label,
                        "page_from": c.page_from, "page_to": c.page_to, "char_start": c.char_start,
                        "char_end": c.char_end, "text": c.text, "quotes": [],
                    }
                    for c in chunks
                ],
            )  # fmt: skip

    async def list(self, document_id: UUID) -> list[Chunk]:
        """Chunks in order; redacted text is returned empty."""
        t = m.document_chunk
        rows = await self._s.execute(select(t).where(t.c.document_id == document_id).order_by(t.c.ord))
        return [
            Chunk(
                id=ChunkId(r.id),
                document_id=r.document_id,
                ord=r.ord,
                page_from=r.page_from,
                page_to=r.page_to,
                char_start=r.char_start,
                char_end=r.char_end,
                text=r.text or "",
            )  # fmt: skip
            for r in rows
        ]

    async def redact(self, document_id: UUID, retained: dict[ChunkId, Retained], now: datetime) -> int:
        """Replace text by retained quotes and summary on every chunk."""
        t = m.document_chunk
        ids = [r[0] for r in await self._s.execute(select(t.c.id).where(t.c.document_id == document_id))]
        if not ids:
            return 0
        params = []
        for cid in ids:
            kept = retained.get(ChunkId(cid))
            params.append(
                {
                    "b_id": cid,
                    "b_quotes": [{"text": q.text, "truncated": q.truncated} for q in kept.quotes]
                    if kept
                    else [],
                    "b_summary": kept.summary if kept else None,
                }
            )
        await self._s.execute(
            update(t).where(t.c.id == bindparam("b_id"))
            .values(text=None, quotes=bindparam("b_quotes"), summary=bindparam("b_summary"), redacted_at=now, updated_at=now),
            params,
        )  # fmt: skip
        return len(ids)

    async def get(self, chunk_id: ChunkId) -> Chunk | None:
        """One chunk by id."""
        t = m.document_chunk
        r = (await self._s.execute(select(t).where(t.c.id == chunk_id))).first()
        if r is None:
            return None
        return Chunk(
            id=ChunkId(r.id), document_id=r.document_id, ord=r.ord, page_from=r.page_from, page_to=r.page_to,
            char_start=r.char_start, char_end=r.char_end, text=r.text or "",
        )  # fmt: skip

    async def retained(self, document_id: UUID) -> dict[ChunkId, Retained]:
        """Quotes and summaries kept on redacted chunks."""
        t = m.document_chunk
        rows = await self._s.execute(
            select(t.c.id, t.c.quotes, t.c.summary).where(
                t.c.document_id == document_id, t.c.redacted_at.is_not(None)
            )
        )
        return {
            ChunkId(r.id): Retained(tuple(Quote(q["text"], q["truncated"]) for q in r.quotes), r.summary)
            for r in rows
            if r.quotes or r.summary
        }

    async def delete_all(self, document_id: UUID) -> int:
        """Delete every chunk of the document."""
        res = await self._s.execute(
            delete(m.document_chunk).where(m.document_chunk.c.document_id == document_id)
        )
        return int(res.rowcount)  # type: ignore[attr-defined]


def _dump(s: StoredInsights) -> dict[str, Any]:
    return {
        "items": [
            {
                "section": i.section.value, "data": i.data, "chunk_labels": list(i.chunk_labels),
                "chunk_ids": [str(c) for c in i.chunk_ids], "status": i.status.value,
                "reason": i.reason.value if i.reason else None, "stage": i.stage.value,
            }
            for i in s.items
        ],
        "keywords": s.keywords, "title": s.title,
        "kind_suggested": s.kind_suggested.value if s.kind_suggested else None, "kind_reason": s.kind_reason,
    }  # fmt: skip


def _load(document_id: UUID, row: Any) -> StoredInsights:
    p = row.payload
    return StoredInsights(
        document_id=document_id,
        items=[
            InsightItem(
                section=Section(i["section"]), data=i["data"], chunk_labels=tuple(i["chunk_labels"]),
                chunk_ids=tuple(ChunkId(UUID(c)) for c in i["chunk_ids"]), status=ItemStatus(i["status"]),
                reason=UnverifiedReason(i["reason"]) if i["reason"] else None, stage=Stage(i["stage"]),
            )  # fmt: skip
            for i in p["items"]
        ],
        keywords=p["keywords"], title=p["title"],
        kind_suggested=DocKind(p["kind_suggested"]) if p["kind_suggested"] else None, kind_reason=p["kind_reason"],
        prompt_version=row.prompt_version, model=row.model, mode=row.mode, slices=row.slices,
        cost_usd=float(row.cost_usd),
    )  # fmt: skip


class SqlInsightsRepo:
    """Implements ``InsightsRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, document_id: UUID) -> StoredInsights | None:
        """Stored insights of a document."""
        row = (
            await self._s.execute(
                select(m.document_insights).where(m.document_insights.c.document_id == document_id)
            )
        ).first()
        return _load(document_id, row) if row else None

    async def upsert(self, insights: StoredInsights) -> None:
        """Insert or replace."""
        verified = sum(1 for i in insights.items if i.status is ItemStatus.VERIFIED)
        values = {
            "payload": _dump(insights), "verified_count": verified,
            "unverified_count": len(insights.items) - verified, "prompt_version": insights.prompt_version,
            "model": insights.model, "mode": insights.mode, "slices": insights.slices,
            "cost_usd": Decimal(str(round(insights.cost_usd, 8))),
        }  # fmt: skip
        stmt = pg_insert(m.document_insights).values(
            document_id=insights.document_id, created_at=func.now(), updated_at=func.now(), **values
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=["document_id"], set_={**values, "updated_at": func.now()}
            )
        )

    async def delete(self, document_id: UUID) -> None:
        """Remove."""
        await self._s.execute(
            delete(m.document_insights).where(m.document_insights.c.document_id == document_id)
        )


class SqlVaultUnitOfWork(SqlUnitOfWork):
    """Vault transaction."""

    documents: SqlDocumentRepo
    chunks: SqlChunkRepo
    insights: SqlInsightsRepo
    audit: AuditLog

    def _bind(self) -> None:
        self.documents = SqlDocumentRepo(self.session)
        self.chunks = SqlChunkRepo(self.session)
        self.insights = SqlInsightsRepo(self.session)
        self.audit = SqlAuditLog(self.session)
