"""``InMemoryLiterature``: a scripted ``LiteraturePort`` for use-case tests (no recorded files needed)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from scout.ports.literature import AuthorCandidate, AuthorRecord, WorkFilter, WorkQuery, WorkRecord
from scout.shared import NotFound


@dataclass
class InMemoryLiterature:
    """Works by author id, topical works (any filter with topics; paged by ``page``), author records, and a log.

    ``fail`` holds method names that raise ``RuntimeError``.
    """

    works_by_author: dict[str, list[WorkRecord]] = field(default_factory=dict)
    topical: list[WorkRecord] = field(default_factory=list)
    authors: dict[str, AuthorRecord] = field(default_factory=dict)
    calls: list[tuple[str, object]] = field(default_factory=list)
    fail: set[str] = field(default_factory=set)

    def _enter(self, name: str, arg: object) -> None:
        self.calls.append((name, arg))
        if name in self.fail:
            raise RuntimeError(f"{name} failed")

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        self._enter("search_authors", name)
        return []

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        self._enter("authors_by_orcid", orcid)
        return []

    async def get_author(self, author_id: str) -> AuthorRecord:
        self._enter("get_author", author_id)
        if author_id not in self.authors:
            raise NotFound(author_id)
        return self.authors[author_id]

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        self._enter("list_author_works", author_id)
        return self.works_by_author.get(author_id, [])[:limit]

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        self._enter("search_works", query.search)
        return []

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        self._enter("get_work", work_id_or_doi)
        return None

    async def count_works(self, query: WorkQuery) -> int:
        self._enter("count_works", query.search)
        return 0

    async def works_by_filter(self, query: WorkFilter) -> list[WorkRecord]:
        self._enter("works_by_filter", query)
        if query.author_id:
            return self.works_by_author.get(query.author_id, [])[: query.per_page]
        if query.topic_ids:
            start = (query.page - 1) * query.per_page
            return self.topical[start : start + query.per_page]
        return []

    async def get_authors(self, author_ids: Sequence[str]) -> list[AuthorRecord]:
        self._enter("get_authors", tuple(author_ids))
        return [self.authors[i] for i in author_ids if i in self.authors]
