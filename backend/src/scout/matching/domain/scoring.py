"""Pure complementarity scoring (ARCHITECTURE §6.3, formula ``c1``) and the weekly proposal cap."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scout.matching.domain.proposal import ScoreBasis
from scout.shared import AccountId


def weighted_jaccard(x: Mapping[str, float], y: Mapping[str, float]) -> float:
    """``sum(min) / sum(max)`` over the union of keys; 0 when either side is empty."""
    if not x or not y:
        return 0.0
    keys = x.keys() | y.keys()
    low = sum(min(x.get(k, 0.0), y.get(k, 0.0)) for k in keys)
    high = sum(max(x.get(k, 0.0), y.get(k, 0.0)) for k in keys)
    return low / high if high > 0 else 0.0


@dataclass(frozen=True)
class ScoredPair:
    """A kept pair with the numbers behind its score."""

    score: float
    basis: ScoreBasis


def score_pair(
    *,
    a_topics: Mapping[str, float],
    b_topics: Mapping[str, float],
    a_methods: Mapping[str, float],
    b_methods: Mapping[str, float],
    a_activity: float,
    b_activity: float,
    alpha: float = 1.0,
    beta: float = 1.5,
    min_score: float = 0.08,
) -> ScoredPair | None:
    """Score one pair: similar topics, different methods, both active.

    ``score = T^alpha * (1 - M)^beta * (0.5 + 0.5 * min(activity))``. Returns None below ``min_score`` or when a
    side has no method the other lacks (nothing to bring).
    """
    a_only = sorted(set(a_methods) - set(b_methods))
    b_only = sorted(set(b_methods) - set(a_methods))
    if not a_only or not b_only:
        return None
    t = weighted_jaccard(a_topics, b_topics)
    m = weighted_jaccard(a_methods, b_methods)
    act = 0.5 + 0.5 * min(a_activity, b_activity)
    score = (t**alpha) * ((1.0 - m) ** beta) * act
    if score < min_score:
        return None
    basis = ScoreBasis(
        topic_overlap=t, method_overlap=m, activity=act, alpha=alpha, beta=beta,
        shared_topics=sorted(set(a_topics) & set(b_topics))[:5], a_only=a_only[:5], b_only=b_only[:5],
        formula="c1",
    )  # fmt: skip
    return ScoredPair(score=min(score, 1.0), basis=basis)


def select_pairs(
    pairs: Sequence[tuple[AccountId, AccountId, float]],
    *,
    open_counts: Mapping[AccountId, int],
    cap: int,
) -> list[tuple[AccountId, AccountId, float]]:
    """Greedy weekly cap: best score first, while both sides stay under ``cap`` proposals in the last 7 days."""
    taken: dict[AccountId, int] = {}
    chosen: list[tuple[AccountId, AccountId, float]] = []
    for a, b, score in sorted(pairs, key=lambda p: (-p[2], p[0], p[1])):
        if all(open_counts.get(x, 0) + taken.get(x, 0) < cap for x in (a, b)):
            chosen.append((a, b, score))
            taken[a] = taken.get(a, 0) + 1
            taken[b] = taken.get(b, 0) + 1
    return chosen
