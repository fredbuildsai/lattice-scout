"""Opportunity scores and duplicate detection (pure). Every score is in [0, 1]."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

EFFORT = {"low": 0.25, "medium": 0.5, "high": 0.85}
_STOP = frozenset(
    "a an and the of for in on to with by at from as is are be using use via new towards toward".split()
)
DUPLICATE_THRESHOLD = 0.6


def effort_value(estimate: str | None) -> float:
    """low 0.25, medium 0.5, high 0.85, anything else 0.5."""
    return EFFORT.get((estimate or "").lower(), 0.5)


def fit_score(topic_weights: Mapping[str, float], topic_ids: Sequence[str], insight_weight: float) -> float:
    """0.6 * mean user weight over the opportunity's topics + 0.4 * min(1, insight weight / 2)."""
    mean = sum(topic_weights.get(t, 0.0) for t in topic_ids) / len(topic_ids) if topic_ids else 0.0
    return min(1.0, 0.6 * min(1.0, mean) + 0.4 * min(1.0, insight_weight / 2))


def readiness_score(present: int, total: int) -> float:
    """Share of checklist items present in the vault (0 for an empty checklist)."""
    return 0.0 if total <= 0 else min(1.0, present / total)


def rank_score(fit: float, novelty: float, readiness: float, effort: float) -> float:
    """0.35 fit + 0.30 novelty + 0.20 readiness + 0.15 (1 - effort)."""
    return 0.35 * fit + 0.30 * novelty + 0.20 * readiness + 0.15 * (1 - effort)


def title_tokens(title: str) -> frozenset[str]:
    """Lower-case alphanumeric tokens without stop words."""
    return frozenset(t for t in re.findall(r"[a-z0-9]+", title.lower()) if t not in _STOP and len(t) > 1)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Token overlap (0 when both are empty)."""
    return len(a & b) / len(a | b) if a | b else 0.0


def is_duplicate(title: str, others: Sequence[str], threshold: float = DUPLICATE_THRESHOLD) -> int | None:
    """Index of the first title in ``others`` whose token Jaccard with ``title`` reaches ``threshold``."""
    mine = title_tokens(title)
    for i, other in enumerate(others):
        if jaccard(mine, title_tokens(other)) >= threshold:
            return i
    return None
