"""``VaultService``: read, classify and delete documents (owner-scoped; another owner's document is a 404)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import structlog

from scout.ports.audit import AuditEntry
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.shared import AccountId, Clock, IdGenerator, NotFound
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind
from scout.vault.domain.ids import ChunkId
from scout.vault.domain.insights import StoredInsights
from scout.vault.ports.repos import VaultUnitOfWork

log = structlog.get_logger()


@dataclass(frozen=True)
class DocumentDetail:
    """A document, its stored insights (callers show ``visible()`` items only) and chunk page ranges."""

    document: Document
    insights: StoredInsights | None
    pages: dict[ChunkId, tuple[int, int]]


class VaultService:
    """Queries and commands on one owner's documents."""

    def __init__(
        self, uow: Callable[[], VaultUnitOfWork], jobs: JobQueuePort, clock: Clock, ids: IdGenerator
    ) -> None:
        self._uow, self._jobs, self._clock, self._ids = uow, jobs, clock, ids

    async def list(self, owner_id: AccountId) -> list[Document]:
        """The owner's documents, newest first."""
        async with self._uow() as uow:
            return await uow.documents.list_for_owner(owner_id)

    async def get(self, owner_id: AccountId, document_id: UUID) -> tuple[Document, StoredInsights | None]:
        """A document with its stored insights.

        Raises:
            NotFound: unknown, deleting, or owned by someone else.
        """
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id, owner_id)
            if doc is None:
                raise NotFound("Document not found")
            return doc, await uow.insights.get(document_id)

    async def get_detail(self, owner_id: AccountId, document_id: UUID) -> DocumentDetail:
        """Like ``get`` but with the page range of every chunk, so verified items can cite pages.

        Raises:
            NotFound: unknown, deleting, or owned by someone else.
        """
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id, owner_id)
            if doc is None:
                raise NotFound("Document not found")
            stored = await uow.insights.get(document_id)
            chunks = await uow.chunks.list(document_id) if stored is not None else []
        pages = {c.id: (c.page_from, c.page_to) for c in chunks}
        return DocumentDetail(doc, stored, pages)

    async def set_kind(self, owner_id: AccountId, document_id: UUID, kind: DocKind) -> Document:
        """Record the owner's classification.

        Raises:
            NotFound: unknown, deleting, or owned by someone else.
        """
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id, owner_id)
            if doc is None:
                raise NotFound("Document not found")
            doc.set_kind(kind, self._clock.now())
            await uow.documents.save(doc)
            await uow.commit()
            return doc

    async def delete(self, owner_id: AccountId, document_id: UUID) -> None:
        """Delete a document: content (chunks, insights) is gone at commit; ``vault.delete`` removes the stored file.

        Raises:
            NotFound: unknown, already deleting, or owned by someone else.
        """
        async with self._uow() as uow:
            doc = await uow.documents.get(document_id, owner_id)
            if doc is None:
                raise NotFound("Document not found")
            locked = await uow.documents.get_for_update(document_id)
            if locked is None or not locked.request_deletion(self._clock.now()):
                raise NotFound("Document not found")
            original_present = locked.original_deleted_at is None
            await uow.documents.save(locked)
            await uow.chunks.delete_all(document_id)
            await uow.insights.delete(document_id)
            await uow.audit.add(
                AuditEntry(
                    id=self._ids.new(),
                    actor_id=owner_id,
                    action="document.deleted",
                    target=str(document_id),
                    at=self._clock.now(),
                    meta={
                        "document_id": str(document_id),
                        "retention": locked.retention.value,
                        "original_present": original_present,
                    },
                )  # fmt: skip
            )
            await uow.commit()
        try:
            await self._jobs.enqueue(
                JobSpec(
                    kind="vault.delete",
                    payload={
                        "account_id": str(owner_id),
                        "feature": "vault.delete",
                        "document_id": str(document_id),
                    },
                    dedupe_key=str(document_id),
                    max_attempts=8,
                )
            )
        except Exception:
            log.error("vault.enqueue_failed", document_id=str(document_id))
