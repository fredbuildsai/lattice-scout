"""External suggestions: researchers who are not on Lattice Scout, found in published OpenAlex data (DECISIONS 132).

Two scopes for now:

* ``in_network``: co-authors of your most frequent co-authors (collaborators of collaborators);
* ``in_field``: researchers outside your network who publish on your topics.

Everything here is pure. External people are never contacted and never enter match proposals or the double opt-in;
a suggestion carries public bibliographic facts and an OpenAlex link only.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from scout.matching.domain.scoring import overlap_coefficient
from scout.shared import InvariantViolation

BRIDGE_COAUTHORS = 15
"""How many of your most frequent co-authors are followed for collaborators of collaborators."""
MAX_LOOKUPS = 50
"""Most candidate authors looked up in detail per refresh (one ``/authors`` call)."""
NETWORK_SHARE = 0.6
"""Share of the lookups reserved for ``in_network`` candidates."""
TOPICAL_PAGES = 2
"""Topical result pages fetched per refresh; the second moves deeper as you have seen more suggestions."""
MAX_TOPICAL_PAGE = 20
"""Pages cycled through before starting over."""
MAX_TOPICS = 25
"""Topics sent in one OpenAlex OR filter (and kept from an author record)."""
RECENT_YEARS = 3
"""Window of the candidate search and of the activity measure."""
MAX_SHARED_TOPICS = 5
FORMULA = "ext1"
"""``score = T^alpha * (0.5 + 0.5 * min(activity))``: no method term, outsiders' methods are unknown."""


class ExternalLayer(StrEnum):
    """Where an external suggestion comes from. Order of the members is the display order."""

    IN_NETWORK = "in_network"
    IN_FIELD = "in_field"


class SuggestionState(StrEnum):
    """What the viewer did with a suggestion."""

    ACTIVE = "active"
    SAVED = "saved"
    DISMISSED = "dismissed"


@dataclass(frozen=True)
class AuthorRef:
    """An author of a work: OpenAlex short id and display name."""

    id: str
    name: str


@dataclass(frozen=True)
class WorkAuthors:
    """The authors of one work, and its last author (likely the principal investigator) when known."""

    authors: tuple[AuthorRef, ...]
    last: AuthorRef | None = None


@dataclass(frozen=True)
class Bridge:
    """One of your co-authors, with the number of works you share."""

    id: str
    name: str
    shared_works: int


@dataclass
class RawCandidate:
    """A candidate author before lookup: how they were found and how often."""

    id: str
    name: str
    layer: ExternalLayer
    vias: list[str] = field(default_factory=list)
    hits: int = 1


@dataclass(frozen=True)
class AuthorTopic:
    """A topic of an OpenAlex author record with the number of the author's works in it."""

    id: str
    name: str
    count: int


@dataclass(frozen=True)
class ExternalAuthor:
    """The public facts of a looked-up OpenAlex author."""

    id: str
    name: str
    institution: str | None = None
    topics: tuple[AuthorTopic, ...] = ()
    works_by_year: Mapping[int, int] = field(default_factory=dict)
    h_index: int = 0
    citations: int = 0
    works_count: int = 0


@dataclass(frozen=True)
class ScoredExternal:
    """A kept candidate with the numbers behind its score."""

    author: ExternalAuthor
    layer: ExternalLayer
    vias: tuple[str, ...]
    score: float
    topic_overlap: float
    shared_topics: tuple[str, ...]
    recent_works: int


class InvalidSuggestionState(InvariantViolation):
    """Only ``active``, ``saved`` and ``dismissed`` exist."""

    code = "invalid_state"
    title = "Invalid suggestion state"


@dataclass
class ExternalSuggestion:
    """A stored suggestion of a researcher who is not on the platform, for one account."""

    id: UUID
    account_id: UUID
    openalex_id: str
    name: str
    institution: str | None
    score: float
    topic_overlap: float
    layer: ExternalLayer
    vias: tuple[str, ...]
    shared_topics: tuple[str, ...]
    h_index: int
    citations: int
    works_count: int
    recent_works: int
    created_at: datetime
    updated_at: datetime
    state: SuggestionState = SuggestionState.ACTIVE
    formula: str = FORMULA

    @classmethod
    def from_scored(
        cls, s: ScoredExternal, *, id: UUID, account_id: UUID, now: datetime
    ) -> ExternalSuggestion:
        """A new active suggestion."""
        a = s.author
        return cls(
            id=id, account_id=account_id, openalex_id=a.id, name=a.name, institution=a.institution, score=s.score,
            topic_overlap=s.topic_overlap, layer=s.layer, vias=s.vias, shared_topics=s.shared_topics,
            h_index=a.h_index, citations=a.citations, works_count=a.works_count, recent_works=s.recent_works,
            created_at=now, updated_at=now,
        )  # fmt: skip

    def set_state(self, state: SuggestionState, now: datetime) -> None:
        """Save, dismiss or restore (back to active)."""
        if not isinstance(state, SuggestionState):
            raise InvalidSuggestionState()
        if state is not self.state:
            self.state, self.updated_at = state, now


def recent_since(now: datetime) -> str:
    """First day of the search window: ``RECENT_YEARS`` years back to the day (ISO date)."""
    try:
        start = now.replace(year=now.year - RECENT_YEARS)
    except ValueError:  # 29 February
        start = now.replace(year=now.year - RECENT_YEARS, day=28)
    return start.date().isoformat()


def topical_pages(seen_count: int) -> list[int]:
    """Page 1 plus a page that moves deeper the more suggestions you have seen (one step per 20)."""
    deep = 2 + (seen_count // 20) % (MAX_TOPICAL_PAGE - 1)
    return list(dict.fromkeys([1, deep]))[:TOPICAL_PAGES]


def top_coauthors(
    works: Iterable[WorkAuthors], own_ids: Iterable[str], n: int = BRIDGE_COAUTHORS
) -> list[Bridge]:
    """Your co-authors by number of shared works, most first (ties by id), at most ``n``."""
    own = set(own_ids)
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for w in works:
        for a in dict.fromkeys(w.authors):  # an author listed twice counts once per work
            if not a.id or a.id in own:
                continue
            counts[a.id] = counts.get(a.id, 0) + 1
            if a.name and not names.get(a.id):
                names[a.id] = a.name
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [Bridge(i, names.get(i, ""), c) for i, c in ranked[:n]]


def direct_coauthors(works: Iterable[WorkAuthors]) -> set[str]:
    """Every author id of your works (yourself included; callers add your ids to the exclusions anyway)."""
    return {a.id for w in works for a in w.authors if a.id}


def collect_candidates(
    bridged: Sequence[tuple[Bridge, Sequence[WorkAuthors]]],
    topical: Iterable[WorkAuthors],
    exclude: set[str],
    pi_only: bool = True,
) -> list[RawCandidate]:
    """Candidate authors from the works of your co-authors (``in_network``) and from works on your topics
    (``in_field``).

    With ``pi_only`` (default) only last authors count. Excluded ids never appear. ``in_network`` wins over
    ``in_field`` for someone found both ways, and collects the names of every co-author who links you. Result:
    ``in_network`` first, then by number of hits, then by id.
    """
    out: dict[str, RawCandidate] = {}

    def add(a: AuthorRef, layer: ExternalLayer, vias: list[str]) -> None:
        if not a.id or a.id in exclude:
            return
        e = out.get(a.id)
        if e is None:
            out[a.id] = RawCandidate(a.id, a.name, layer, list(vias))
            return
        e.hits += 1
        if not e.name and a.name:
            e.name = a.name
        if layer is ExternalLayer.IN_NETWORK:
            if e.layer is not ExternalLayer.IN_NETWORK:
                e.layer, e.vias = layer, list(vias)
            else:
                e.vias = list(dict.fromkeys([*e.vias, *vias]))

    def pick(w: WorkAuthors) -> list[AuthorRef]:
        if pi_only:
            return [w.last] if w.last is not None else []
        return list(dict.fromkeys(w.authors))

    for bridge, works in bridged:
        via = [bridge.name] if bridge.name else []
        for w in works:
            for a in pick(w):
                add(a, ExternalLayer.IN_NETWORK, via)
    for w in topical:
        for a in pick(w):
            add(a, ExternalLayer.IN_FIELD, [])
    order = list(ExternalLayer)
    return sorted(out.values(), key=lambda r: (order.index(r.layer), -r.hits, r.id))


def lookup_pool(
    candidates: Sequence[RawCandidate], max_lookups: int = MAX_LOOKUPS, network_share: float = NETWORK_SHARE
) -> list[RawCandidate]:
    """The candidates worth one detailed lookup: up to ``network_share`` of the slots for ``in_network``, the rest
    filled with ``in_field`` (and with more ``in_network`` when there are too few ``in_field``)."""
    network = [c for c in candidates if c.layer is ExternalLayer.IN_NETWORK]
    field_ = [c for c in candidates if c.layer is ExternalLayer.IN_FIELD]
    reserved = network[: math.ceil(max_lookups * network_share)]
    pool = [*reserved, *field_][:max_lookups]
    if len(pool) < max_lookups:
        pool += network[len(reserved) : len(reserved) + max_lookups - len(pool)]
    return pool


def author_topic_weights(author: ExternalAuthor, limit: int = MAX_TOPICS) -> dict[str, float]:
    """Topic weights of an author record: counts of the first ``limit`` topics, normalised to sum 1."""
    kept = [t for t in author.topics if t.id and t.count > 0][:limit]
    total = sum(t.count for t in kept)
    return {t.id: t.count / total for t in kept} if total else {}


def recent_works_count(author: ExternalAuthor, current_year: int) -> int:
    """Works in the current year and the two before."""
    return sum(n for y, n in author.works_by_year.items() if y > current_year - RECENT_YEARS)


def author_activity(author: ExternalAuthor, current_year: int) -> float:
    """``min(1, recent works / 3)``, the same rule as for members."""
    return min(1.0, recent_works_count(author, current_year) / 3)


def score_external(
    my_topics: Mapping[str, float],
    my_activity: float,
    their_topics: Mapping[str, float],
    their_activity: float,
    *,
    alpha: float = 1.0,
    min_topic: float = 0.1,
    min_score: float = 0.11,
) -> tuple[float, float] | None:
    """``(score, T)`` with ``T`` the overlap coefficient and ``score = T^alpha * (0.5 + 0.5 * min(activity))``;
    None when ``T < min_topic`` or ``score < min_score``."""
    t = overlap_coefficient(my_topics, their_topics)
    if t <= 0 or t < min_topic:
        return None
    act = min(1.0, max(0.0, min(my_activity, their_activity)))
    score = min(1.0, (t**alpha) * (0.5 + 0.5 * act))
    if score < min_score:
        return None
    return score, t


def shared_topic_names(
    my_topics: Mapping[str, float], their_topics: Mapping[str, float], names: Mapping[str, str]
) -> tuple[str, ...]:
    """Names of the topics both sides have, strongest combined weight first, at most five."""
    shared = sorted(
        (k for k in my_topics if k in their_topics), key=lambda k: (-(my_topics[k] + their_topics[k]), k)
    )
    return tuple(names.get(k, k) for k in shared[:MAX_SHARED_TOPICS])


def rank_external(
    my_topics: Mapping[str, float],
    my_activity: float,
    candidates: Sequence[RawCandidate],
    authors: Mapping[str, ExternalAuthor],
    *,
    current_year: int,
    size: int,
    alpha: float = 1.0,
    min_topic: float = 0.1,
    min_score: float = 0.11,
    my_topic_names: Mapping[str, str] | None = None,
) -> list[ScoredExternal]:
    """Score every looked-up candidate; keep those above the thresholds; ``in_network`` first, then by score
    (ties by id); at most ``size``."""
    out: list[ScoredExternal] = []
    for c in candidates:
        a = authors.get(c.id)
        if a is None:
            continue
        theirs = author_topic_weights(a)
        kept = score_external(
            my_topics, my_activity, theirs, author_activity(a, current_year),
            alpha=alpha, min_topic=min_topic, min_score=min_score,
        )  # fmt: skip
        if kept is None:
            continue
        names = {**(my_topic_names or {}), **{t.id: t.name for t in a.topics if t.name}}
        if not a.name and c.name:
            a = replace(a, name=c.name)
        out.append(
            ScoredExternal(
                author=a,
                layer=c.layer,
                vias=tuple(c.vias),
                score=round(kept[0], 4),
                topic_overlap=round(kept[1], 4),
                shared_topics=shared_topic_names(my_topics, theirs, names),
                recent_works=recent_works_count(a, current_year),
            )  # fmt: skip
        )
    order = list(ExternalLayer)
    out.sort(key=lambda s: (order.index(s.layer), -s.score, s.author.id))
    return out[:size]
