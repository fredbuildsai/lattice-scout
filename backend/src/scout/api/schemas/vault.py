"""DTOs for the vault endpoints. Insights are verified items only; unverified ones never leave the backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from scout.vault.domain.enums import DocKind, DocStatus, DocumentMime, Retention


class RefOut(BaseModel):
    """Where in the document an item is stated."""

    chunk_id: UUID = Field(description="Stable chunk id (also used as evidence reference).")
    page_from: int = Field(description="First page (or section, when the file has no page numbers).")
    page_to: int = Field(description="Last page.")


class ItemOut(BaseModel):
    """A statement with its sources."""

    summary: str
    refs: list[RefOut]


class MethodOut(BaseModel):
    """A method or technique."""

    name: str
    detail: str | None = None
    refs: list[RefOut]


class DatasetOut(BaseModel):
    """A dataset the document mentions."""

    name: str
    status: Literal["available", "planned", "partial"]
    refs: list[RefOut]


class ObjectionOut(BaseModel):
    """A reviewer objection: ``quote`` is verbatim from the document."""

    quote: str
    summary: str
    refs: list[RefOut]


class InsightsOut(BaseModel):
    """What Scout extracted from a document (verified items only)."""

    title: str | None = None
    aims: list[ItemOut]
    findings: list[ItemOut]
    open_questions: list[ItemOut]
    methods: list[MethodOut]
    datasets: list[DatasetOut]
    reviewer_objections: list[ObjectionOut]
    keywords: list[str]
    prompt_version: str


class ClearVaultIn(BaseModel):
    """Typed confirmation for deleting the whole vault."""

    model_config = ConfigDict(extra="forbid")

    confirm: str = Field(max_length=20, description="The word ``DELETE``.")


class ClearedOut(BaseModel):
    """How many documents were marked for deletion by this call."""

    deleting: int = Field(description="Documents newly marked for deletion (0 on a repeat call).")


class DocumentOut(BaseModel):
    """A document in the vault."""

    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    filename: str
    mime: DocumentMime
    size_bytes: int
    status: DocStatus = Field(
        description="uploaded, extracting, extracted, analysing, ready, failed or rejected."
    )
    failure_code: str | None = Field(default=None, description="Why a failed or rejected document stopped.")
    kind: DocKind | None = Field(
        default=None, description="Your classification, otherwise Scout's suggestion."
    )
    kind_source: Literal["user", "scout"] | None = None
    kind_suggested: DocKind | None = None
    retention: Retention = Field(
        description="keep: the original is stored; discard: only extracted content is kept."
    )
    original_available: bool
    pages: int | None = None
    chunk_count: int | None = None
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None = None


class DocumentListOut(BaseModel):
    """All documents of the vault, newest first."""

    items: list[DocumentOut]
    next_cursor: str | None = Field(default=None, description="Reserved for paging; always null for now.")


class DocumentDetailOut(DocumentOut):
    """A document with its insights."""

    insights: InsightsOut | None = None


class DocumentPatchIn(BaseModel):
    """Change the classification."""

    model_config = ConfigDict(extra="forbid")

    kind: DocKind
