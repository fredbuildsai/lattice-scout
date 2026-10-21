"""Vault persistence ports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from scout.ports.audit import AuditLog
from scout.shared import AccountId, Conflict, UnitOfWork
from scout.vault.domain.chunking import Chunk
from scout.vault.domain.document import Document
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import StoredInsights
from scout.vault.domain.retention import Retained


class DuplicateDocument(Conflict):
    """Another active document of the owner has the same content hash."""

    code = "duplicate_document"
    title = "Document already in the vault"


@dataclass(frozen=True)
class StaleSets:
    """Document ids the sweeper must act on."""

    uploaded: tuple[UUID, ...] = ()
    purge: tuple[UUID, ...] = ()
    deleting: tuple[UUID, ...] = ()
    stalled: tuple[UUID, ...] = ()
    awaiting: tuple[
        UUID, ...
    ] = ()  # waiting for an authorized provider; the sweep requeues them once one exists


class DocumentRepo(Protocol):
    """Document storage."""

    async def get(self, document_id: UUID, owner_id: AccountId) -> Document | None:
        """Owner's document; ``None`` for another owner's or a ``deleting`` one."""
        ...

    async def get_for_update(self, document_id: UUID) -> Document | None:
        """Worker side: any owner, row locked until the transaction ends."""
        ...

    async def list_for_owner(self, owner_id: AccountId) -> list[Document]:
        """Owner's documents, newest first, without ``deleting`` ones."""
        ...

    async def find_active_by_sha(self, owner_id: AccountId, sha256: str) -> Document | None:
        """Active (not failed, rejected or deleting) document with the same hash."""
        ...

    async def count_active(self, owner_id: AccountId) -> int:
        """Documents that count against the vault limit."""
        ...

    async def count_uploaded_since(self, owner_id: AccountId, since: datetime) -> int:
        """Uploads by the owner since ``since`` (counts deleted ones too: the audit trail keeps them)."""
        ...

    async def add(self, document: Document) -> None:
        """Insert. Raises ``DuplicateDocument`` when the unique index is hit."""
        ...

    async def save(self, document: Document) -> None:
        """Persist changes."""
        ...

    async def hard_delete(self, document_id: UUID) -> None:
        """Remove the row (after the stored file is gone)."""
        ...

    async def list_stale(self, older_than: datetime, stalled_before: datetime, limit: int) -> StaleSets:
        """Documents the sweeper must re-enqueue or fail."""
        ...


class ChunkRepo(Protocol):
    """Chunk storage."""

    async def replace_all(self, document_id: UUID, chunks: Sequence[Chunk]) -> None:
        """Replace the chunks of a document (idempotent)."""
        ...

    async def list(self, document_id: UUID) -> list[Chunk]:
        """Chunks in order; discarded text is returned as an empty string."""
        ...

    async def redact(self, document_id: UUID, retained: dict[ChunkId, Retained], now: datetime) -> int:
        """Replace every chunk's text by its retained quotes and summary; return how many chunks were redacted."""
        ...

    async def get(self, chunk_id: ChunkId) -> Chunk | None:
        """One chunk (text is empty when it was redacted); ownership is checked through its document."""
        ...

    async def retained(self, document_id: UUID) -> dict[ChunkId, Retained]:
        """Quotes and summaries kept for redacted chunks."""
        ...

    async def delete_all(self, document_id: UUID) -> int:
        """Delete all chunks; return how many."""
        ...


class InsightsRepo(Protocol):
    """Insight storage (1:1 with a document)."""

    async def get(self, document_id: UUID) -> StoredInsights | None:
        """Stored insights."""
        ...

    async def upsert(self, insights: StoredInsights) -> None:
        """Insert or replace."""
        ...

    async def delete(self, document_id: UUID) -> None:
        """Remove."""
        ...


class VaultUnitOfWork(UnitOfWork, Protocol):
    """Transaction for vault use cases."""

    @property
    def documents(self) -> DocumentRepo:
        """Documents."""
        ...

    @property
    def chunks(self) -> ChunkRepo:
        """Chunks."""
        ...

    @property
    def insights(self) -> InsightsRepo:
        """Insights."""
        ...

    @property
    def audit(self) -> AuditLog:
        """Audit log."""
        ...
