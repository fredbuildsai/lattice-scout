"""Researcher topic weights computed from included works."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from scout.profile.domain.work import ClaimedWork


class ResearcherTopic(BaseModel):
    """A topic of the researcher with weight in [0, 1] (max = 1)."""

    model_config = ConfigDict(frozen=True)

    topic_id: str
    name: str | None = None
    subfield: str | None = None
    weight: float = Field(ge=0.0, le=1.0)


def compute_topics(works: Iterable[ClaimedWork], max_topics: int = 25) -> list[ResearcherTopic]:
    """Sum topic scores (missing = 1.0) over **included** works, divide by the maximum, sort by weight then id."""
    acc: dict[str, float] = defaultdict(float)
    meta: dict[str, tuple[str | None, str | None]] = {}
    for w in works:
        if not w.included:
            continue
        for t in w.topics:
            acc[t.id] += t.weight if t.weight > 0 else 1.0
            meta.setdefault(t.id, (t.name, t.subfield))
    if not acc:
        return []
    top = max(acc.values())
    ranked = sorted(acc, key=lambda k: (-acc[k], k))[:max_topics]
    return [
        ResearcherTopic(topic_id=k, name=meta[k][0], subfield=meta[k][1], weight=acc[k] / top) for k in ranked
    ]
