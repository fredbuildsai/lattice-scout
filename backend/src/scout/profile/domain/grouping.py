"""Grouping of OpenAlex author records into candidate profiles (split-profile merge)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from scout.profile.domain.names import names_compatible


class RecordBasis(StrEnum):
    """Why a record belongs to a profile."""

    PRIMARY = "primary"
    ORCID = "orcid"
    INSTITUTION_NAME = "institution_name"
    MANUAL = "manual"


class TopicWeight(BaseModel):
    """A topic with a weight in [0, 1]."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str | None = None
    subfield: str | None = None
    weight: float = Field(default=0.0, ge=0.0, le=1.0)


class AuthorSnapshot(BaseModel):
    """Author record as seen by the profile domain (mapped from the literature port in the application layer)."""

    model_config = ConfigDict(frozen=True)

    id: str
    display_name: str
    orcid: str | None = None
    works_count: int = 0
    cited_by_count: int = 0
    institutions: tuple[str, ...] = ()
    country: str | None = None
    topics: tuple[TopicWeight, ...] = ()


class GroupedRecord(BaseModel):
    """A record inside a candidate group."""

    model_config = ConfigDict(frozen=True)

    snapshot: AuthorSnapshot
    basis: RecordBasis


@dataclass(frozen=True)
class CandidateGroup:
    """Records that likely belong to one person, plus weakly linked ``possible`` records (never merged)."""

    primary: AuthorSnapshot
    records: tuple[GroupedRecord, ...]
    possible: tuple[GroupedRecord, ...] = field(default=())

    @property
    def includes_records(self) -> int:
        """Number of merged records."""
        return len(self.records)

    @property
    def works_count(self) -> int:
        """Sum of works over merged records."""
        return sum(r.snapshot.works_count for r in self.records)

    @property
    def cited_by_count(self) -> int:
        """Sum of citations over merged records."""
        return sum(r.snapshot.cited_by_count for r in self.records)

    @property
    def orcid(self) -> str | None:
        """ORCID of the primary record."""
        return self.primary.orcid

    @property
    def institutions(self) -> tuple[str, ...]:
        """Ordered union of institutions over merged records."""
        seen: dict[str, None] = {}
        for r in self.records:
            for i in r.snapshot.institutions:
                seen.setdefault(i, None)
        return tuple(seen)

    def top_topics(self, n: int = 5) -> list[TopicWeight]:
        """Topic weights summed over records and renormalised to a maximum of 1."""
        acc: dict[str, float] = defaultdict(float)
        meta: dict[str, TopicWeight] = {}
        for r in self.records:
            for t in r.snapshot.topics:
                acc[t.id] += t.weight
                meta.setdefault(t.id, t)
        if not acc:
            return []
        top = max(acc.values()) or 1.0
        ranked = sorted(acc, key=lambda k: (-acc[k], k))[:n]
        return [meta[k].model_copy(update={"weight": round(acc[k] / top, 6)}) for k in ranked]


def _primary_key(s: AuthorSnapshot) -> tuple[int, int, str]:
    return (-s.works_count, 0 if s.institutions else 1, s.id)


def group_candidates(
    snaps: list[AuthorSnapshot], affiliation_hint: str | None = None
) -> list[CandidateGroup]:
    """Group author snapshots. Pure and deterministic (independent of input order).

    1. Dedupe by id. 2. Records with an equal non-null ORCID merge (primary: most works). 3. An ORCID-less record
    sharing an institution with an ORCID group and a compatible name becomes a ``possible`` row of the largest
    such group; weak links never chain or merge. 4. Everything else is a singleton group.
    """
    by_id: dict[str, AuthorSnapshot] = {}
    for s in sorted(snaps, key=lambda x: x.id):
        if s.id in by_id:
            by_id[s.id] = by_id[s.id].model_copy(
                update={"institutions": tuple(dict.fromkeys(by_id[s.id].institutions + s.institutions))}
            )
        else:
            by_id[s.id] = s
    by_orcid: dict[str, list[AuthorSnapshot]] = defaultdict(list)
    loose: list[AuthorSnapshot] = []
    for s in by_id.values():
        (by_orcid[s.orcid] if s.orcid else loose).append(s)

    groups: list[tuple[AuthorSnapshot, list[GroupedRecord], list[GroupedRecord]]] = []
    for members in by_orcid.values():
        members.sort(key=_primary_key)
        primary = members[0]
        recs = [GroupedRecord(snapshot=primary, basis=RecordBasis.PRIMARY)] + [
            GroupedRecord(snapshot=m, basis=RecordBasis.ORCID) for m in members[1:]
        ]
        groups.append((primary, recs, []))

    singletons: list[AuthorSnapshot] = []
    for s in sorted(loose, key=lambda x: x.id):
        sinst = {i.casefold() for i in s.institutions}
        candidates = [
            g for g in groups
            if any(
                {i.casefold() for i in r.snapshot.institutions} & sinst and names_compatible(r.snapshot.display_name, s.display_name)
                for r in g[1]
            )
        ]  # fmt: skip
        if sinst and candidates:
            best = sorted(candidates, key=lambda g: (-g[0].works_count, g[0].id))[0]
            best[2].append(GroupedRecord(snapshot=s, basis=RecordBasis.INSTITUTION_NAME))
        else:
            singletons.append(s)
    for s in singletons:
        groups.append((s, [GroupedRecord(snapshot=s, basis=RecordBasis.PRIMARY)], []))

    hint = affiliation_hint.casefold() if affiliation_hint else None

    def order(g: tuple[AuthorSnapshot, list[GroupedRecord], list[GroupedRecord]]) -> tuple[int, int, str]:
        matches = hint is not None and any(
            hint in i.casefold() for r in g[1] for i in r.snapshot.institutions
        )
        return (0 if matches else 1, -sum(r.snapshot.works_count for r in g[1]), g[0].id)

    return [
        CandidateGroup(primary=p, records=tuple(r), possible=tuple(sorted(pos, key=lambda x: x.snapshot.id)))
        for p, r, pos in sorted(groups, key=order)
    ]
