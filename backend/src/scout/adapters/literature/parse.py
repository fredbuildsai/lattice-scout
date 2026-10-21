"""Parse OpenAlex JSON (live API and recorded fixtures share the same shape) into port records."""

from __future__ import annotations

from typing import Any

from scout.ports.literature import AuthorRecord, TopicRef, WorkRecord


def short_id(value: str | None) -> str:
    """``https://openalex.org/A123`` -> ``A123``."""
    return (value or "").rsplit("/", 1)[-1]


def bare_orcid(value: str | None) -> str | None:
    """``https://orcid.org/0000-...`` -> ``0000-...``."""
    return value.rsplit("/", 1)[-1] if value else None


def bare_doi(value: str | None) -> str | None:
    """Strip the doi.org resolver prefix."""
    if not value:
        return None
    return value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")


def parse_topic(t: dict[str, Any]) -> TopicRef:
    """Topic object -> ``TopicRef``."""
    sub = t.get("subfield") or {}
    return TopicRef(
        id=short_id(t.get("id")), name=t.get("display_name"), subfield=sub.get("display_name"),
        count=t.get("count"), score=t.get("score"),
    )  # fmt: skip


def parse_author(a: dict[str, Any]) -> AuthorRecord:
    """Author object -> ``AuthorRecord``."""
    insts = a.get("last_known_institutions") or []
    return AuthorRecord(
        id=short_id(a.get("id")),
        display_name=a.get("display_name") or "",
        orcid=bare_orcid(a.get("orcid")),
        works_count=a.get("works_count") or 0,
        cited_by_count=a.get("cited_by_count") or 0,
        h_index=(a.get("summary_stats") or {}).get("h_index"),
        institutions=tuple(i.get("display_name") for i in insts if i.get("display_name")),
        country=next((i.get("country_code") for i in insts if i.get("country_code")), None),
        topics=tuple(parse_topic(t) for t in a.get("topics") or []),
    )


def abstract_from_inverted(inv: dict[str, list[int]] | None) -> str | None:
    """Rebuild an abstract from OpenAlex's ``abstract_inverted_index``."""
    if not inv:
        return None
    words: dict[int, str] = {}
    for word, positions in inv.items():
        for p in positions:
            words[p] = word
    return " ".join(words[i] for i in sorted(words))


def parse_work(w: dict[str, Any]) -> WorkRecord:
    """Work object -> ``WorkRecord``."""
    auth = [x.get("author") or {} for x in w.get("authorships") or []]
    return WorkRecord(
        id=short_id(w.get("id")),
        doi=bare_doi(w.get("doi")),
        title=w.get("title") or w.get("display_name") or "",
        year=w.get("publication_year"),
        cited_by_count=w.get("cited_by_count") or 0,
        type=w.get("type"),
        abstract=abstract_from_inverted(w.get("abstract_inverted_index")),
        topics=tuple(parse_topic(t) for t in w.get("topics") or []),
        author_ids=tuple(short_id(a.get("id")) for a in auth if a.get("id")),
        author_names=tuple(a.get("display_name") for a in auth if a.get("display_name")),
    )
