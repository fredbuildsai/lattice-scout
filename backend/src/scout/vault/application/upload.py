"""``UploadDocument``: validate, store the bytes, insert the row, enqueue ingestion."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog

from scout.ports.audit import AuditEntry
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.storage import FileStoragePort
from scout.shared import AccountId, Clock, Conflict, IdGenerator, RateLimited
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocumentMime, RetentionPolicy
from scout.vault.domain.ids import storage_key
from scout.vault.domain.upload import (
    FileRejected,
    FileTooLarge,
    UnsupportedFileType,
    check_zip,
    clean_filename,
    resolve_retention,
    sniff_mime,
)
from scout.vault.ports.repos import DuplicateDocument, VaultUnitOfWork

log = structlog.get_logger()


class UploadLimitReached(RateLimited):
    """Too many uploads in 24 hours."""

    code = "upload_limit_reached"
    title = "Daily upload limit reached"


class VaultLimitReached(Conflict):
    """The vault already holds the maximum number of documents."""

    code = "vault_limit_reached"
    title = "Your vault is full"


def _inspect(data: bytes) -> tuple[DocumentMime, str]:
    """Detect the type, run the archive checks and hash the bytes."""
    mime = sniff_mime(data)
    if mime is None:
        raise UnsupportedFileType("Only PDF and DOCX files are accepted")
    if mime is DocumentMime.DOCX:
        check_zip(data)
    return mime, hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class UploadResult:
    """The document and whether this call created it (``False``: the same bytes were already in the vault)."""

    document: Document
    created: bool


class UploadDocument:
    """Accept one file into the owner's vault."""

    def __init__(
        self,
        uow: Callable[[], VaultUnitOfWork],
        storage: FileStoragePort,
        jobs: JobQueuePort,
        clock: Clock,
        ids: IdGenerator,
        policy: RetentionPolicy,
        max_bytes: int,
        max_documents: int,
        max_per_day: int = 0,
    ) -> None:
        self._uow, self._storage, self._jobs, self._clock, self._ids = uow, storage, jobs, clock, ids
        self._policy, self._max_bytes, self._max_documents = policy, max_bytes, max_documents
        self._max_per_day = max_per_day

    async def upload(
        self,
        owner_id: AccountId,
        filename: str,
        data: bytes,
        keep_original: bool | None,
        kind_hint: DocKind | None,
        session_id: UUID | None = None,
    ) -> UploadResult:
        """Store ``data`` and queue ``vault.ingest``.

        Raises:
            FileTooLarge: empty or above the size limit.
            UnsupportedFileType: not a PDF or DOCX by content.
            FileRejected: a DOCX that is a zip bomb or corrupt.
            UploadLimitReached: more than ``max_per_day`` uploads in 24 hours.
            Conflict: the vault already holds the maximum number of documents (``vault_limit_reached``).
        """
        if not data:
            raise FileRejected("empty_file")
        if len(data) > self._max_bytes:
            raise FileTooLarge("The file is larger than the limit")
        mime, sha = await asyncio.to_thread(_inspect, data)  # CPU work off the event loop

        async with self._uow() as uow:
            existing = await uow.documents.find_active_by_sha(owner_id, sha)
            if existing is not None:
                return UploadResult(existing, False)
            if await uow.documents.count_active(owner_id) >= self._max_documents:
                raise VaultLimitReached("vault_limit_reached", extra={"limit": self._max_documents})
            if self._max_per_day and (
                await uow.documents.count_uploaded_since(owner_id, self._clock.now() - timedelta(hours=24))
                >= self._max_per_day
            ):
                raise UploadLimitReached("Daily upload limit reached", extra={"limit": self._max_per_day})

        document_id = self._ids.new()
        key = storage_key(owner_id, document_id)
        await self._storage.put(key, data, mime.value)  # outside any transaction
        now = self._clock.now()
        retention = resolve_retention(self._policy, keep_original)
        doc = Document.upload(
            id_=document_id, owner_id=owner_id, filename=clean_filename(filename), mime=mime, size_bytes=len(data),
            sha256=sha, storage_key=key, retention=retention, kind_hint=kind_hint, now=now,
        )  # fmt: skip
        try:
            async with self._uow() as uow:
                await uow.documents.add(doc)
                await uow.audit.add(
                    AuditEntry(
                        id=self._ids.new(),
                        actor_id=owner_id,
                        action="document.uploaded",
                        target=str(document_id),
                        at=now,
                        meta={
                            "document_id": str(document_id),
                            "mime": mime.value,
                            "size_bytes": len(data),
                            "retention": retention.value,
                        },
                    )  # fmt: skip
                )
                await uow.commit()
        except DuplicateDocument:  # a parallel upload of the same bytes won the race
            await self._discard_object(key)
            async with self._uow() as uow:
                winner = await uow.documents.find_active_by_sha(owner_id, sha)
            if winner is None:
                raise
            return UploadResult(winner, False)
        except BaseException:
            await self._discard_object(key)
            raise
        doc.pull_events()
        try:
            await self._jobs.enqueue(
                JobSpec(
                    kind="vault.ingest",
                    payload={
                        "account_id": str(owner_id),
                        "session_id": str(session_id) if session_id else None,
                        "feature": "vault.ingest",
                        "document_id": str(document_id),
                    },
                    dedupe_key=str(document_id),
                    max_attempts=4,
                )  # fmt: skip
            )
        except Exception:
            log.error("vault.enqueue_failed", document_id=str(document_id))
        return UploadResult(doc, True)

    async def _discard_object(self, key: str) -> None:
        try:
            await self._storage.delete(key)
        except Exception:
            log.error("vault.orphan_object", key=key)
