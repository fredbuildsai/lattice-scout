"""Literature port (OpenAlex in production, recorded fixtures offline) and its record types."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class TopicRef(BaseModel):
    """An OpenAlex topic attached to an author or a work."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    subfield: str | None = None
    count: int | None = Field(default=None, description="Number of the author's works in this topic.")
    score: float | None = None


class AuthorRecord(BaseModel):
    """An OpenAlex author record (subset)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Short id, e.g. A5059588273.")
    display_name: str
    orcid: str | None = Field(default=None, description="Bare ORCID, e.g. 0000-0002-4103-8510.")
    works_count: int = 0
    cited_by_count: int = 0
    h_index: int | None = None
    institutions: tuple[str, ...] = ()
    country: str | None = None
    topics: tuple[TopicRef, ...] = ()


class AuthorCandidate(AuthorRecord):
    """Search hit; same shape as ``AuthorRecord``."""


class WorkRecord(BaseModel):
    """An OpenAlex work (subset)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(description="Short id, e.g. W2799396764.")
    doi: str | None = Field(default=None, description="Bare DOI without the resolver prefix.")
    title: str
    year: int | None = None
    cited_by_count: int = 0
    type: str | None = None
    abstract: str | None = None
    topics: tuple[TopicRef, ...] = ()
    author_ids: tuple[str, ...] = ()
    author_names: tuple[str, ...] = ()


class WorkQuery(BaseModel):
    """Title/abstract search with filters."""

    model_config = ConfigDict(frozen=True)

    search: str
    from_publication_date: str | None = None
    type: str | None = None
    per_page: int = 25


class LiteraturePort(Protocol):
    """Read-only access to bibliographic data."""

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        """Candidate authors for a name."""
        ...

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        """All author records sharing an ORCID (split profiles)."""
        ...

    async def get_author(self, author_id: str) -> AuthorRecord:
        """One author record; raises ``NotFound`` if unknown."""
        ...

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        """Works of an author (cursor-paged by the adapter), newest first when the source allows."""
        ...

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        """Works matching a query."""
        ...

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        """One work or None."""
        ...

    async def count_works(self, query: WorkQuery) -> int:
        """Number of works matching a query (novelty measurement)."""
        ...
