"""Map literature-port records to profile domain objects (the domain may not import the port)."""

from __future__ import annotations

from uuid import UUID

from scout.ports.literature import AuthorRecord, TopicRef, WorkRecord
from scout.profile.domain.grouping import AuthorSnapshot, TopicWeight
from scout.profile.domain.work import ClaimedWork, UncertainReason, initial_inclusion


def topic_weights(topics: tuple[TopicRef, ...]) -> tuple[TopicWeight, ...]:
    """Author topics are weighted by their share of the author's works; work topics by OpenAlex score."""
    counts = [t.count for t in topics if t.count is not None]
    top = max(counts) if counts else None
    out: list[TopicWeight] = []
    for t in topics:
        if top and t.count is not None:
            w = t.count / top
        else:
            w = t.score if t.score is not None else 0.0
        out.append(TopicWeight(id=t.id, name=t.name, subfield=t.subfield, weight=max(0.0, min(1.0, w))))
    return tuple(out)


def to_snapshot(a: AuthorRecord) -> AuthorSnapshot:
    """Author record -> domain snapshot."""
    return AuthorSnapshot(
        id=a.id, display_name=a.display_name, orcid=a.orcid, works_count=a.works_count,
        cited_by_count=a.cited_by_count, institutions=a.institutions, country=a.country,
        topics=topic_weights(a.topics),
    )  # fmt: skip


def to_claimed_work(
    w: WorkRecord, researcher_id: UUID, work_row_id: UUID, author_id: str, variants: list[str]
) -> tuple[ClaimedWork, UncertainReason | None]:
    """Work record -> ``ClaimedWork`` plus the reason it starts unticked (if any)."""
    names = [n for aid, n in zip(w.author_ids, w.author_names, strict=False) if aid == author_id]
    reason = initial_inclusion(w.type, names, variants)
    work = ClaimedWork(
        id=work_row_id, researcher_id=researcher_id, openalex_work_id=w.id, title=w.title, doi=w.doi, year=w.year,
        cited_by=w.cited_by_count, type=w.type, abstract=w.abstract, topics=topic_weights(w.topics),
        source_author_ids=frozenset({author_id}), included=reason is None, uncertain_reason=reason,
    )  # fmt: skip
    return work, reason
