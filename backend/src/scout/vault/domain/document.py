"""``Document`` aggregate and its state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar
from uuid import UUID

from scout.shared import AccountId, AggregateRoot, DomainEvent, InvalidStateTransition
from scout.shared.ids import UuidGenerator
from scout.vault.domain.enums import DocKind, DocStatus, DocumentMime, PageBasis, Retention

S = DocStatus
_IN_PROGRESS = {S.UPLOADED, S.EXTRACTING, S.EXTRACTED, S.ANALYSING}
_TERMINAL = {S.READY, S.FAILED, S.REJECTED}
_PURGEABLE = _TERMINAL | {S.AWAITING_PROVIDER}  # extraction is done: the bytes are not needed again


class DocumentUploaded(DomainEvent):
    """A file entered the vault."""

    name: ClassVar[str] = "vault.document_uploaded"
    document_id: UUID


class DocumentReady(DomainEvent):
    """Ingestion finished."""

    name: ClassVar[str] = "vault.document_ready"
    document_id: UUID


class DocumentRejected(DomainEvent):
    """The file cannot be processed (permanent)."""

    name: ClassVar[str] = "vault.document_rejected"
    document_id: UUID
    code: str


class OriginalDiscarded(DomainEvent):
    """The original bytes were deleted under the ``discard`` policy."""

    name: ClassVar[str] = "vault.original_discarded"
    document_id: UUID


class DocumentDeleted(DomainEvent):
    """The owner deleted the document."""

    name: ClassVar[str] = "vault.document_deleted"
    document_id: UUID


@dataclass
class Document(AggregateRoot):
    """A private document of one account."""

    id: UUID
    owner_id: AccountId
    filename: str
    mime: DocumentMime
    size_bytes: int
    sha256: str
    storage_key: str
    retention: Retention
    created_at: datetime
    updated_at: datetime
    status: DocStatus = DocStatus.UPLOADED
    kind: DocKind | None = None
    kind_suggested: DocKind | None = None
    kind_reason: str | None = None
    failure_code: str | None = None
    pages: int | None = None
    page_basis: PageBasis | None = None
    chunk_count: int | None = None
    extractor: str | None = None
    original_deleted_at: datetime | None = None
    extracted_at: datetime | None = None
    ready_at: datetime | None = None
    deleted_at: datetime | None = None

    @classmethod
    def upload(
        cls,
        *,
        id_: UUID,
        owner_id: AccountId,
        filename: str,
        mime: DocumentMime,
        size_bytes: int,
        sha256: str,
        storage_key: str,
        retention: Retention,
        kind_hint: DocKind | None,
        now: datetime,
    ) -> Document:
        """Create a document in ``uploaded``."""
        doc = cls(
            id=id_, owner_id=owner_id, filename=filename, mime=mime, size_bytes=size_bytes, sha256=sha256,
            storage_key=storage_key, retention=retention, kind=kind_hint, created_at=now, updated_at=now,
        )  # fmt: skip
        doc.record_event(DocumentUploaded(event_id=UuidGenerator().new(), occurred_at=now, document_id=id_))
        return doc

    # derived -----------------------------------------------------------------
    @property
    def effective_kind(self) -> DocKind | None:
        """The user's choice, otherwise Scout's suggestion."""
        return self.kind or self.kind_suggested

    @property
    def original_available(self) -> bool:
        """Whether the uploaded bytes still exist."""
        return (
            self.retention is Retention.KEEP
            and self.original_deleted_at is None
            and self.status is not S.DELETING
        )

    @property
    def in_progress(self) -> bool:
        """Whether ingestion is not finished."""
        return self.status in _IN_PROGRESS

    @property
    def needs_purge(self) -> bool:
        """Discard policy, extraction finished (ready, failed, rejected or waiting for a provider), original still
        stored. A waiting document keeps its extracted text until it is analysed, but not the uploaded file."""
        return (
            self.retention is Retention.DISCARD
            and self.status in _PURGEABLE
            and self.original_deleted_at is None
        )

    # transitions -------------------------------------------------------------
    def _require(self, action: str, allowed: set[DocStatus]) -> None:
        if self.status not in allowed:
            raise InvalidStateTransition(f"Cannot {action} a document that is {self.status.value}")

    def _touch(self, now: datetime) -> None:
        self.updated_at = now

    def start_extraction(self, now: datetime) -> None:
        """``uploaded|extracting`` -> ``extracting`` (a re-run after a crash is allowed)."""
        self._require("start extraction of", {S.UPLOADED, S.EXTRACTING})
        self.status = S.EXTRACTING
        self._touch(now)

    def finish_extraction(
        self, *, pages: int, basis: PageBasis, chunks: int, extractor: str, now: datetime
    ) -> None:
        """``extracting`` -> ``extracted``."""
        self._require("finish extraction of", {S.EXTRACTING})
        self.status, self.pages, self.page_basis, self.chunk_count, self.extractor = (
            S.EXTRACTED,
            pages,
            basis,
            chunks,
            extractor,
        )
        self.extracted_at = now
        self._touch(now)

    def reject(self, code: str, now: datetime) -> None:
        """``extracting`` -> ``rejected`` (permanent file problem)."""
        self._require("reject", {S.EXTRACTING})
        self.status, self.failure_code = S.REJECTED, code
        self._touch(now)
        self.record_event(
            DocumentRejected(event_id=UuidGenerator().new(), occurred_at=now, document_id=self.id, code=code)
        )

    def start_analysis(self, now: datetime) -> None:
        """``extracted|analysing`` -> ``analysing``."""
        self._require("start analysis of", {S.EXTRACTED, S.ANALYSING, S.AWAITING_PROVIDER})
        self.status = S.ANALYSING
        self._touch(now)

    def await_provider(self, now: datetime) -> None:
        """``extracted|analysing|awaiting`` -> ``awaiting_approved_provider``: no authorized endpoint can read the
        text, so the document is kept and analysed later (ADR 016). Not a failure and not a purge trigger."""
        self._require("wait for a provider with", {S.EXTRACTED, S.ANALYSING, S.AWAITING_PROVIDER})
        self.status = S.AWAITING_PROVIDER
        self._touch(now)

    def finish_analysis(self, kind_suggested: DocKind | None, reason: str | None, now: datetime) -> None:
        """``analysing`` -> ``ready``."""
        self._require("finish analysis of", {S.ANALYSING})
        self.status, self.kind_suggested, self.kind_reason, self.ready_at = (
            S.READY,
            kind_suggested,
            reason,
            now,
        )
        self._touch(now)
        self.record_event(DocumentReady(event_id=UuidGenerator().new(), occurred_at=now, document_id=self.id))

    def fail(self, code: str, now: datetime) -> None:
        """Any in-progress status -> ``failed``."""
        self._require("fail", _IN_PROGRESS)
        self.status, self.failure_code = S.FAILED, code
        self._touch(now)

    def mark_original_deleted(self, now: datetime) -> bool:
        """Record that the bytes were purged. Idempotent; returns True the first time."""
        if self.retention is not Retention.DISCARD:
            raise InvalidStateTransition("Only documents with the discard policy have their original purged")
        self._require("purge the original of", _PURGEABLE)
        if self.original_deleted_at is not None:
            return False
        self.original_deleted_at = now
        self._touch(now)
        self.record_event(
            OriginalDiscarded(event_id=UuidGenerator().new(), occurred_at=now, document_id=self.id)
        )
        return True

    def set_kind(self, kind: DocKind, now: datetime) -> None:
        """The owner's classification, allowed in any status but ``deleting``."""
        self._require("set the kind of", set(DocStatus) - {S.DELETING})
        self.kind = kind
        self._touch(now)

    def request_deletion(self, now: datetime) -> bool:
        """Any status -> ``deleting`` (idempotent; returns False when already deleting)."""
        if self.status is S.DELETING:
            return False
        self.status, self.deleted_at = S.DELETING, now
        self._touch(now)
        self.record_event(
            DocumentDeleted(event_id=UuidGenerator().new(), occurred_at=now, document_id=self.id)
        )
        return True
