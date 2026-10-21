"""Read-only views of other contexts (profile, vault) as the discovery context needs them (anti-corruption layer).

Implemented in ``application/sources.py`` on top of the profile and vault services; the discovery domain never
imports those contexts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from scout.shared import AccountId


@dataclass(frozen=True)
class ProfileWork:
    """One of the researcher's included works."""

    openalex_id: str
    title: str
    abstract: str | None
    year: int | None
    cited_by: int
    type: str | None
    doi: str | None
    topics: tuple[tuple[str, str | None, str | None], ...]  # (topic id, name, subfield)


@dataclass(frozen=True)
class RunProfile:
    """The claimed profile as a run needs it."""

    researcher_id: UUID
    topics: tuple[tuple[str, str | None, str | None, float], ...]  # (id, name, subfield, weight)
    works: tuple[ProfileWork, ...]  # included works, newest first
    import_done: bool


@dataclass(frozen=True)
class InsightView:
    """A verified vault insight (never an unverified one)."""

    ref: str  # d1.aims[0]
    section: str
    text: str
    chunk_labels: tuple[str, ...]
    page_from: int | None = None
    page_to: int | None = None


@dataclass(frozen=True)
class PassageView:
    """A cited vault passage. ``keep``: the chunk text. ``discard``: only the retained verified quotes."""

    label: str  # d1.c12
    chunk_id: UUID
    document_id: UUID
    page_from: int
    page_to: int
    retention: Literal["keep", "discard"]
    text: str | None
    quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocMaterial:
    """What a run may use of one document."""

    document_id: UUID
    label: str  # d1
    filename: str
    retention: Literal["keep", "discard"]
    active: bool
    insights: tuple[InsightView, ...] = ()
    passages: tuple[PassageView, ...] = ()
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentCheck:
    """Whether a document may be attached to a run."""

    document_id: UUID
    exists: bool
    ready: bool
    retention: Literal["keep", "discard"] = "discard"


class ProfileSource(Protocol):
    """The researcher's profile."""

    async def run_profile(self, owner_id: AccountId) -> RunProfile | None:
        """The claimed profile, or None when there is none."""
        ...


class VaultSource(Protocol):
    """The owner's vault, restricted to what discovery may read."""

    async def check_documents(
        self, owner_id: AccountId, document_ids: tuple[UUID, ...]
    ) -> list[DocumentCheck]:
        """Existence (owner-scoped, not deleting) and readiness of each document, in request order."""
        ...

    async def run_material(
        self, owner_id: AccountId, labelled: tuple[tuple[str, UUID], ...]
    ) -> list[DocMaterial]:
        """Verified insights and cited passages for the labelled documents (``(label, document_id)``).

        Documents that disappeared come back with ``active=False`` and no content.
        """
        ...

    async def passage(self, owner_id: AccountId, chunk_id: UUID) -> PassageView | None:
        """One passage of an owned, active document (None otherwise)."""
        ...


__all__ = [
    "DocMaterial",
    "DocumentCheck",
    "InsightView",
    "PassageView",
    "ProfileSource",
    "ProfileWork",
    "RunProfile",
    "VaultSource",
]
