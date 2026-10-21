"""In-memory vault repositories and unit of work (staged copies; ``commit`` publishes)."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from scout.shared import AccountId
from scout.vault.domain.chunking import Chunk
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocStatus, Retention
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import StoredInsights
from scout.vault.domain.retention import Retained
from scout.vault.ports.repos import DuplicateDocument, StaleSets
from tests.fakes.audit import InMemoryAuditLog

_INACTIVE = {DocStatus.FAILED, DocStatus.REJECTED, DocStatus.DELETING}
_TERMINAL = {DocStatus.READY, DocStatus.FAILED, DocStatus.REJECTED}


@dataclass
class ChunkRow:
    chunk: Chunk
    text: str | None
    retained: Retained | None = None
    redacted_at: datetime | None = None


@dataclass
class VaultStore:
    """Committed state."""

    documents: dict[UUID, Document] = field(default_factory=dict)
    chunks: dict[UUID, list[ChunkRow]] = field(default_factory=dict)
    insights: dict[UUID, StoredInsights] = field(default_factory=dict)
    audit: InMemoryAuditLog = field(default_factory=InMemoryAuditLog)
    commits: int = 0


class InMemoryDocumentRepo:
    def __init__(self, data: dict[UUID, Document]) -> None:
        self.data = data

    async def get(self, document_id: UUID, owner_id: AccountId) -> Document | None:
        d = self.data.get(document_id)
        ok = d is not None and d.owner_id == owner_id and d.status is not DocStatus.DELETING
        return copy.deepcopy(d) if ok else None

    async def get_for_update(self, document_id: UUID) -> Document | None:
        return copy.deepcopy(self.data.get(document_id))

    async def list_for_owner(self, owner_id: AccountId) -> list[Document]:
        docs = [
            d for d in self.data.values() if d.owner_id == owner_id and d.status is not DocStatus.DELETING
        ]
        return [copy.deepcopy(d) for d in sorted(docs, key=lambda d: (-d.created_at.timestamp(), str(d.id)))]

    async def find_active_by_sha(self, owner_id: AccountId, sha256: str) -> Document | None:
        return next(
            (
                copy.deepcopy(d)
                for d in self.data.values()
                if d.owner_id == owner_id and d.sha256 == sha256 and d.status not in _INACTIVE
            ),
            None,
        )

    async def count_active(self, owner_id: AccountId) -> int:
        return sum(
            1 for d in self.data.values() if d.owner_id == owner_id and d.status is not DocStatus.DELETING
        )

    async def count_uploaded_since(self, owner_id: AccountId, since: datetime) -> int:
        return sum(1 for d in self.data.values() if d.owner_id == owner_id and d.created_at >= since)

    async def add(self, document: Document) -> None:
        if document.status not in _INACTIVE and await self.find_active_by_sha(
            document.owner_id, document.sha256
        ):
            raise DuplicateDocument("duplicate")
        self.data[document.id] = copy.deepcopy(document)

    async def save(self, document: Document) -> None:
        self.data[document.id] = copy.deepcopy(document)

    async def hard_delete(self, document_id: UUID) -> None:
        self.data.pop(document_id, None)

    async def list_stale(self, older_than: datetime, stalled_before: datetime, limit: int) -> StaleSets:
        docs = sorted(self.data.values(), key=lambda d: d.created_at)

        def ids(pred: object) -> tuple[UUID, ...]:
            return tuple(d.id for d in docs if pred(d))[:limit]  # type: ignore[operator]

        return StaleSets(
            uploaded=ids(lambda d: d.status is DocStatus.UPLOADED and d.updated_at < older_than),
            purge=ids(lambda d: d.needs_purge and d.updated_at < older_than),
            deleting=ids(lambda d: d.status is DocStatus.DELETING and d.updated_at < older_than),
            stalled=ids(
                lambda d: (
                    d.status in {DocStatus.EXTRACTING, DocStatus.ANALYSING} and d.updated_at < stalled_before
                )
            ),
            awaiting=ids(lambda d: d.status is DocStatus.AWAITING_PROVIDER),
        )


class InMemoryChunkRepo:
    def __init__(self, data: dict[UUID, list[ChunkRow]]) -> None:
        self.data = data

    async def replace_all(self, document_id: UUID, chunks: Sequence[Chunk]) -> None:
        self.data[document_id] = [ChunkRow(c, c.text) for c in chunks]

    async def list(self, document_id: UUID) -> list[Chunk]:
        return [replace(r.chunk, text=r.text or "") for r in self.data.get(document_id, [])]

    async def redact(self, document_id: UUID, retained: dict[ChunkId, Retained], now: datetime) -> int:
        rows = self.data.get(document_id, [])
        for r in rows:
            r.text, r.retained, r.redacted_at = None, retained.get(r.chunk.id), now
        return len(rows)

    async def get(self, chunk_id: ChunkId) -> Chunk | None:
        for rows in self.data.values():
            for r in rows:
                if r.chunk.id == chunk_id:
                    return replace(r.chunk, text=r.text or "")
        return None

    async def retained(self, document_id: UUID) -> dict[ChunkId, Retained]:
        return {r.chunk.id: r.retained for r in self.data.get(document_id, []) if r.retained is not None}

    async def delete_all(self, document_id: UUID) -> int:
        return len(self.data.pop(document_id, []))


class InMemoryInsightsRepo:
    def __init__(self, data: dict[UUID, StoredInsights]) -> None:
        self.data = data

    async def get(self, document_id: UUID) -> StoredInsights | None:
        return copy.deepcopy(self.data.get(document_id))

    async def upsert(self, insights: StoredInsights) -> None:
        self.data[insights.document_id] = copy.deepcopy(insights)

    async def delete(self, document_id: UUID) -> None:
        self.data.pop(document_id, None)


class InMemoryVaultUoW:
    def __init__(self, store: VaultStore) -> None:
        self._store = store

    async def __aenter__(self) -> Self:
        s = self._store
        self._d = {k: copy.deepcopy(v) for k, v in s.documents.items()}
        self._c = {k: copy.deepcopy(v) for k, v in s.chunks.items()}
        self._i = {k: copy.deepcopy(v) for k, v in s.insights.items()}
        self._documents = InMemoryDocumentRepo(self._d)
        self._chunks = InMemoryChunkRepo(self._c)
        self._insights = InMemoryInsightsRepo(self._i)
        self._audit = InMemoryAuditLog()
        return self

    @property
    def documents(self) -> InMemoryDocumentRepo:
        return self._documents

    @property
    def chunks(self) -> InMemoryChunkRepo:
        return self._chunks

    @property
    def insights(self) -> InMemoryInsightsRepo:
        return self._insights

    @property
    def audit(self) -> InMemoryAuditLog:
        return self._audit

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        s = self._store
        for target, staged in ((s.documents, self._d), (s.chunks, self._c), (s.insights, self._i)):
            target.clear()
            target.update(staged)  # type: ignore[arg-type]
        s.audit.entries.extend(self._audit.entries)
        s.commits += 1

    async def rollback(self) -> None:
        return None


__all__ = ["InMemoryVaultUoW", "Retention", "VaultStore"]
