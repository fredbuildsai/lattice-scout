"""Where the researcher already has material, and which clusters are candidate gaps (pure)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

MAX_CANDIDATES = 8
CORE_WEIGHT = 1.0
PARTIAL_WEIGHT = 0.5
SUBFIELD_ADJACENCY = 0.3


@dataclass(frozen=True)
class ClusterStats:
    """Inputs of the coverage maths for one cluster."""

    key: str
    size: int
    subfield: str | None
    user_works: int
    insight_weight: float
    is_misc: bool = False


def tag_weight(strength: str) -> float:
    """Weight of one insight tag: ``core`` 1.0, ``partial`` 0.5, anything else 0."""
    return {"core": CORE_WEIGHT, "partial": PARTIAL_WEIGHT}.get(strength, 0.0)


def user_coverage(user_works: int, insight_weight: float) -> float:
    """0..1: half from the researcher's published works (3 saturate), half from vault insights (weight 2 saturates)."""
    return min(1.0, 0.5 * min(1.0, user_works / 3) + 0.5 * min(1.0, insight_weight / 2))


def density(size: int, max_size: int) -> float:
    """How crowded a cluster is relative to the largest one in the run (0..1)."""
    return 0.0 if max_size <= 0 else min(1.0, size / max_size)


def candidate_scores(clusters: Sequence[ClusterStats]) -> dict[str, float]:
    """``adjacency * (1 - density)`` per non-misc cluster.

    Adjacency is the researcher's coverage when positive; otherwise 0.3 for a cluster sharing a subfield with a
    cluster the researcher covers at 0.5 or more; otherwise 0.
    """
    real = [c for c in clusters if not c.is_misc]
    largest = max((c.size for c in real), default=0)
    cover = {c.key: user_coverage(c.user_works, c.insight_weight) for c in real}
    anchored = {c.subfield for c in real if c.subfield and cover[c.key] >= 0.5}
    out: dict[str, float] = {}
    for c in real:
        adjacency = (
            cover[c.key] if cover[c.key] > 0 else (SUBFIELD_ADJACENCY if c.subfield in anchored else 0.0)
        )
        out[c.key] = adjacency * (1 - density(c.size, largest))
    return out


def pick_candidates(clusters: Sequence[ClusterStats], limit: int = MAX_CANDIDATES) -> tuple[list[str], bool]:
    """Top ``limit`` clusters with a positive score (best first; ties by key).

    Returns ``(keys, fallback)``. With no positive score the three largest non-misc clusters are returned and
    ``fallback`` is True (the caller records the degradation ``no_adjacent_clusters``).
    """
    scores = candidate_scores(clusters)
    ranked = sorted((k for k, s in scores.items() if s > 0), key=lambda k: (-scores[k], k))
    if ranked:
        return ranked[:limit], False
    sizes = {c.key: c.size for c in clusters if not c.is_misc}
    return sorted(sizes, key=lambda k: (-sizes[k], k))[:3], True
