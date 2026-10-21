"""Pure computation of a researcher's matching profile: weighted topics, weighted methods, coarse facets."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from scout.matching.domain.agent_io import MethodMention
from scout.matching.domain.enums import CareerBand
from scout.matching.domain.vocabulary import Vocabulary

WINDOW_YEARS = 5
MAX_TOPICS = 25
MIN_TOPIC_WEIGHT = 0.05
MAX_METHOD_WORKS = 20
CONFIDENCE = {"high": 1.0, "medium": 0.5}


@dataclass(frozen=True)
class WorkIn:
    """An included work as the profile computation sees it."""

    ref: str  # work:W123
    title: str
    abstract: str | None
    year: int | None
    cited_by: int
    topics: tuple[tuple[str, str | None], ...] = ()  # (topic id, name)


@dataclass(frozen=True)
class TopicWeight:
    """A topic with its normalised weight."""

    id: str
    name: str
    weight: float


@dataclass
class MethodWeights:
    """Method weights per permitted source, plus the labels the model proposed that are not in the vocabulary."""

    abstract: dict[str, float] = field(default_factory=dict)
    vault: dict[str, float] = field(default_factory=dict)
    proposals: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def effective(self, use_vault: bool) -> dict[str, float]:
        """Max over the permitted sources."""
        out = dict(self.abstract)
        if use_vault:
            for k, v in self.vault.items():
                out[k] = max(out.get(k, 0.0), v)
        return out


def window_works(works: Sequence[WorkIn], now: datetime) -> list[WorkIn]:
    """Works of the last five years; all dated works when there are fewer than three."""
    recent = [w for w in works if w.year is not None and w.year > now.year - WINDOW_YEARS]
    return recent if len(recent) >= 3 else [w for w in works if w.year is not None] or list(works)


def compute_topics(works: Sequence[WorkIn], now: datetime) -> list[TopicWeight]:
    """Recency and citation weighted topic set, normalised to a maximum of 1, at most 25 topics."""
    win = window_works(works, now)
    top_cited = max((w.cited_by for w in win), default=0)
    acc: dict[str, float] = {}
    names: dict[str, str] = {}
    for w in win:
        recency = max(0.1, 1.0 - 0.1 * (now.year - (w.year or now.year)))
        cite = (w.cited_by / top_cited) if top_cited > 0 else 0.0
        weight = recency * (0.5 + 0.5 * cite)
        for tid, name in w.topics:
            acc[tid] = acc.get(tid, 0.0) + weight
            names.setdefault(tid, name or tid)
    if not acc:
        return []
    peak = max(acc.values())
    ranked = sorted(((tid, v / peak) for tid, v in acc.items()), key=lambda x: (-x[1], x[0]))
    return [TopicWeight(t, names[t], round(v, 4)) for t, v in ranked[:MAX_TOPICS] if v >= MIN_TOPIC_WEIGHT]


def method_works(works: Sequence[WorkIn]) -> list[WorkIn]:
    """The newest works with an abstract, at most 20 (the MethodExtractor input)."""
    dated = sorted((w for w in works if w.abstract), key=lambda w: (-(w.year or 0), w.ref))
    return dated[:MAX_METHOD_WORKS]


def weigh_methods(
    mentions: Iterable[MethodMention], vocabulary: Vocabulary, valid_refs: Mapping[str, str]
) -> MethodWeights:
    """Turn model mentions into weights.

    ``valid_refs`` maps each input ref to its source (``abstract`` or ``vault``). Unknown ids and refs are dropped; a
    mention left without refs is dropped. Weights are the confidence summed over refs, scaled by the largest.
    """
    known = vocabulary.ids()
    raw: dict[str, dict[str, float]] = {"abstract": {}, "vault": {}}
    out = MethodWeights()
    for m in mentions:
        refs = tuple(dict.fromkeys(r for r in m.source_refs if r in valid_refs))
        if not refs:
            continue
        if m.method_id is None:
            if m.new_label and m.new_label.strip():
                out.proposals.append((m.new_label.strip(), refs))
            continue
        if m.method_id not in known:
            continue
        for r in refs:
            bucket = raw[valid_refs[r]]
            bucket[m.method_id] = bucket.get(m.method_id, 0.0) + CONFIDENCE[m.confidence]
    peak = max((v for b in raw.values() for v in b.values()), default=0.0)
    if peak > 0:
        out.abstract = {k: round(v / peak, 4) for k, v in raw["abstract"].items()}
        out.vault = {k: round(v / peak, 4) for k, v in raw["vault"].items()}
    return out


def career_band(works: Sequence[WorkIn], now: datetime) -> CareerBand:
    """Years since the first work: up to 7 early, 8 to 15 mid, more senior; unknown without dated works."""
    years = [w.year for w in works if w.year]
    if not years:
        return CareerBand.UNKNOWN
    span = now.year - min(years)
    return CareerBand.EARLY if span <= 7 else CareerBand.MID if span <= 15 else CareerBand.SENIOR


def activity(works: Sequence[WorkIn], now: datetime) -> float:
    """Works of the last three years, capped at three, as a share of three."""
    return min(1.0, sum(1 for w in works if w.year and w.year > now.year - 3) / 3)


def is_eligible(
    consent_matching: bool, import_done: bool, topics: Sequence[TopicWeight], methods: Mapping[str, float]
) -> bool:
    """In the pool only with consent, a finished import, at least three topics and one method."""
    return consent_matching and import_done and len(topics) >= 3 and len(methods) >= 1


def methods_hash(sources: Iterable[tuple[str, str]], vocabulary_version: str, prompt_version: str) -> str:
    """Hash of everything that decides the method call; the model runs only when it changes."""
    h = hashlib.sha256()
    for ref, text in sorted(sources):
        h.update(f"{ref}\x00{hashlib.sha256(text.encode()).hexdigest()}\x01".encode())
    h.update(f"{vocabulary_version}\x02{prompt_version}".encode())
    return h.hexdigest()


def topics_hash(topics: Iterable[TopicWeight]) -> str:
    """Hash of the topic set."""
    h = hashlib.sha256()
    for t in sorted(topics, key=lambda t: t.id):
        h.update(f"{t.id}:{t.weight}\x01".encode())
    return h.hexdigest()


@dataclass(frozen=True)
class MatchProfile:
    """A researcher's matching profile as stored."""

    account_id: UUID
    researcher_id: UUID
    field: str
    region: str
    career_band: str
    activity: float
    eligible: bool
    topics: tuple[TopicWeight, ...]
    methods: Mapping[str, float]  # effective, consent applied
    method_sources: Mapping[str, str]  # method id -> abstract|vault
    topics_hash: str
    methods_hash: str
    vocabulary_version: str
    methods_prompt_version: str
    uses_vault_signals: bool
    computed_at: datetime
