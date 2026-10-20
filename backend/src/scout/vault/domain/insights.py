"""Insight types: the LLM output contract and the stored, verified form."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from scout.vault.domain.enums import DocKind
from scout.vault.domain.ids import ChunkId

MAX_ITEMS = 8


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Item(_Out):
    """A statement with the chunks that support it (``chunk_ids`` are labels such as ``c12``)."""

    summary: str
    chunk_ids: list[str] = Field(default_factory=list)


class Method(_Out):
    """A method or technique."""

    name: str
    detail: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)


class Dataset(_Out):
    """A dataset the document mentions."""

    name: str
    status: Literal["available", "planned", "partial"] = "available"
    chunk_ids: list[str] = Field(default_factory=list)


class Objection(_Out):
    """A reviewer objection: ``quote`` is verbatim, ``summary`` is a paraphrase."""

    quote: str
    summary: str
    chunk_ids: list[str] = Field(default_factory=list)


class DocumentInsights(_Out):
    """Typed output of the ``InsightExtractor`` agent."""

    kind: DocKind = DocKind.OTHER
    kind_reason: str | None = None
    title: str | None = None
    aims: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    findings: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    methods: list[Method] = Field(default_factory=list, max_length=MAX_ITEMS)
    datasets: list[Dataset] = Field(default_factory=list, max_length=MAX_ITEMS)
    reviewer_objections: list[Objection] = Field(default_factory=list, max_length=MAX_ITEMS)
    open_questions: list[Item] = Field(default_factory=list, max_length=MAX_ITEMS)
    keywords: list[str] = Field(default_factory=list, max_length=12)


class Section(StrEnum):
    """Insight sections."""

    AIMS = "aims"
    FINDINGS = "findings"
    METHODS = "methods"
    DATASETS = "datasets"
    REVIEWER_OBJECTIONS = "reviewer_objections"
    OPEN_QUESTIONS = "open_questions"


class ItemStatus(StrEnum):
    """Verification result."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


class UnverifiedReason(StrEnum):
    """Why an item is not shown."""

    NO_CHUNK_REF = "no_chunk_ref"
    UNKNOWN_CHUNK = "unknown_chunk"
    PERSONAL_DATA = "personal_data"
    QUOTE_TOO_SHORT = "quote_too_short"
    QUOTE_NOT_FOUND = "quote_not_found"


class Stage(StrEnum):
    """Which LLM call produced the item."""

    FULL = "full"
    MAP = "map"
    REDUCE = "reduce"


@dataclass(frozen=True)
class InsightItem:
    """One stored item. Unverified items are persisted but never returned to users."""

    section: Section
    data: dict[str, Any]
    chunk_labels: tuple[str, ...]
    chunk_ids: tuple[ChunkId, ...]
    status: ItemStatus
    reason: UnverifiedReason | None
    stage: Stage

    @property
    def summary_text(self) -> str:
        """Short text describing the item (used for retained summaries)."""
        d = self.data
        if self.section is Section.METHODS:
            return " - ".join(x for x in (d.get("name"), d.get("detail")) if x)
        if self.section is Section.DATASETS:
            return str(d.get("name", ""))
        return str(d.get("summary", ""))


@dataclass
class StoredInsights:
    """All insights of a document."""

    document_id: UUID
    items: list[InsightItem]
    keywords: list[str]
    title: str | None
    kind_suggested: DocKind | None
    kind_reason: str | None
    prompt_version: str
    model: str
    mode: Literal["full", "map_reduce"]
    slices: int
    cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def visible(self) -> list[InsightItem]:
        """Verified items only: the only ones any user-facing payload may contain."""
        return [i for i in self.items if i.status is ItemStatus.VERIFIED]
