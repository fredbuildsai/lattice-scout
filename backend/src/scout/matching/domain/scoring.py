"""Pure complementarity scoring (ARCHITECTURE §6.3, formulas ``c2`` current and ``c1`` legacy) and the weekly cap."""

from __future__ import annotations

import math
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


def overlap_coefficient(x: Mapping[str, float], y: Mapping[str, float]) -> float:
    """``sum(min) / min(sum x, sum y)``: the share of the lighter profile that the other covers; 0 when either is empty."""
    if not x or not y:
        return 0.0
    low = sum(min(v, y[k]) for k, v in x.items() if k in y)
    smaller = min(sum(x.values()), sum(y.values()))
    return min(1.0, low / smaller) if smaller > 0 else 0.0


def cosine(x: Mapping[str, float], y: Mapping[str, float]) -> float:
    """Cosine similarity of two weight vectors; 0 when either is empty."""
    nx, ny = math.sqrt(sum(v * v for v in x.values())), math.sqrt(sum(v * v for v in y.values()))
    if nx == 0 or ny == 0:
        return 0.0
    return min(1.0, sum(v * y[k] for k, v in x.items() if k in y) / (nx * ny))


TOPIC_SIMILARITY = {"c1": weighted_jaccard, "c2": overlap_coefficient}
CURRENT_FORMULA = "c2"
DEFAULT_MIN_SCORE = 0.11  # DECISIONS 112; calibrated with scripts/calibrate_matching.py


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
    min_score: float = DEFAULT_MIN_SCORE,
    formula: str = CURRENT_FORMULA,
) -> ScoredPair | None:
    """Score one pair: similar topics, different methods, both active.

    ``score = T^alpha * (1 - M)^beta * (0.5 + 0.5 * min(activity))`` with ``M`` the weighted Jaccard of the methods
    and ``T`` the topic similarity of the formula: ``c2`` (default) the overlap coefficient, ``c1`` (legacy, kept so
    old proposals stay readable and for the calibration script) the weighted Jaccard. Returns None below
    ``min_score`` or when a side has no method the other lacks (nothing to bring).

    Raises:
        ValueError: ``formula`` is not ``c1`` or ``c2``.
    """
    if formula not in TOPIC_SIMILARITY:
        raise ValueError(f"unknown score formula {formula!r}")
    a_only = sorted(set(a_methods) - set(b_methods))
    b_only = sorted(set(b_methods) - set(a_methods))
    if not a_only or not b_only:
        return None
    t = TOPIC_SIMILARITY[formula](a_topics, b_topics)
    m = weighted_jaccard(a_methods, b_methods)
    act = 0.5 + 0.5 * min(a_activity, b_activity)
    score = (t**alpha) * ((1.0 - m) ** beta) * act
    if score < min_score:
        return None
    basis = ScoreBasis(
        topic_overlap=t, method_overlap=m, activity=act, alpha=alpha, beta=beta,
        shared_topics=sorted(set(a_topics) & set(b_topics))[:5], a_only=a_only[:5], b_only=b_only[:5],
        formula=formula,
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
