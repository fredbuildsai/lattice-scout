"""FixtureLiterature: serves recorded OpenAlex responses (tests/fixtures/openalex) through ``LiteraturePort``."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import structlog

from scout.adapters.literature.parse import parse_author, parse_work, short_id
from scout.ports.literature import AuthorCandidate, AuthorRecord, WorkFilter, WorkQuery, WorkRecord
from scout.shared import NotFound

log = structlog.get_logger()
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


class FixtureLiterature:
    """Offline literature source. Exact manifest match first; unknown search/count queries fall back to the
    closest recorded query by token overlap (with a warning) so fake pipelines always run."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._manifest: list[dict[str, Any]] = json.loads((root / "manifest.json").read_text())["entries"]
        self._works_cache: dict[str, WorkRecord] | None = None

    def _load(self, file: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((self._root / file).read_text())
        return data

    def _entries(self, method: str) -> list[dict[str, Any]]:
        return [e for e in self._manifest if e["port_method"] == method]

    def _closest(self, method: str, text: str, key: str) -> dict[str, Any]:
        entries = self._entries(method)
        want = _tokens(text)
        best = max(entries, key=lambda e: len(want & _tokens(e["params"][key])))
        if best["params"][key] != text:
            log.warning("fixture_query_fallback", method=method)
        return best

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        """Recorded candidate list for the closest recorded name."""
        entries = [e for e in self._entries("search_authors") if "name" in e["params"]]
        want = _tokens(name)
        scored = [(len(want & _tokens(e["params"]["name"])), e) for e in entries]
        score, best = max(scored, key=lambda x: x[0])
        if score == 0:
            return []
        results = self._load(best["file"])["results"]
        out = [AuthorCandidate(**parse_author(r).model_dump()) for r in results]
        if affiliation_hint:
            hint = affiliation_hint.lower()
            out.sort(key=lambda c: not any(hint in i.lower() for i in c.institutions))
        return out

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        """Recorded records sharing the ORCID."""
        for e in self._entries("search_authors"):
            if e["params"].get("orcid") == orcid:
                return [parse_author(r) for r in self._load(e["file"])["results"]]
        return []

    async def get_author(self, author_id: str) -> AuthorRecord:
        """Recorded author, or ``NotFound``."""
        for e in self._entries("get_author"):
            if e["params"]["author_id"] == short_id(author_id):
                return parse_author(self._load(e["file"]))
        raise NotFound(f"Author {author_id} is not in the fixtures")

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        """Recorded works of the author (empty list if none recorded)."""
        for e in self._entries("list_author_works"):
            if e["params"]["author_id"] == short_id(author_id):
                works = [parse_work(w) for w in self._load(e["file"])["results"]]
                return sorted(works, key=lambda w: w.year or 0, reverse=True)[:limit]
        return []

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        """Works of the closest recorded query."""
        e = self._closest("search_works", query.search, "search")
        return [parse_work(w) for w in self._load(e["file"])["results"]][: query.per_page]

    async def count_works(self, query: WorkQuery) -> int:
        """Count of the closest recorded count query."""
        e = self._closest("count_works", query.search, "search")
        return int(self._load(e["file"])["meta"]["count"])

    def _all_works(self) -> dict[str, WorkRecord]:
        if self._works_cache is None:
            cache: dict[str, WorkRecord] = {}
            for e in self._manifest:
                if e["port_method"] in ("list_author_works", "search_works"):
                    for w in self._load(e["file"])["results"]:
                        rec = parse_work(w)
                        cache.setdefault(rec.id, rec)
            self._works_cache = cache
        return self._works_cache

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        """Find a work by short id, URL or DOI in any recorded file."""
        key = short_id(work_id_or_doi).lower().removeprefix("doi:")
        for w in self._all_works().values():
            if w.id.lower() == key or (
                w.doi and w.doi.lower() == work_id_or_doi.lower().removeprefix("https://doi.org/")
            ):
                return w
        return None

    async def works_by_filter(self, query: WorkFilter) -> list[WorkRecord]:
        """Best effort over the recordings: the author's recorded works, or every recorded work in the topics;
        then the date filter, the sort (``cited_by_count:desc`` or newest first) and the page."""
        if query.author_id:
            works = await self.list_author_works(query.author_id)
        elif query.topic_ids:
            want = {short_id(t) for t in query.topic_ids}
            works = [w for w in self._all_works().values() if want & {t.id for t in w.topics}]
        else:
            return []
        if query.from_publication_date:
            since = int(query.from_publication_date[:4])
            works = [w for w in works if (w.year or 0) >= since]
        if query.sort == "cited_by_count:desc":
            works = sorted(works, key=lambda w: (-w.cited_by_count, w.id))
        else:
            works = sorted(works, key=lambda w: (-(w.year or 0), w.id))
        start = (query.page - 1) * query.per_page
        return works[start : start + query.per_page]

    async def get_authors(self, author_ids: Sequence[str]) -> list[AuthorRecord]:
        """Recorded authors among the ids (``get_author`` recordings first, then search hits)."""
        known: dict[str, AuthorRecord] = {}
        for e in self._entries("search_authors"):
            for r in self._load(e["file"])["results"]:
                rec = parse_author(r)
                known.setdefault(rec.id, rec)
        for e in self._entries("get_author"):
            rec = parse_author(self._load(e["file"]))
            known[rec.id] = rec
        return [known[i] for i in dict.fromkeys(short_id(a) for a in author_ids) if i in known]
