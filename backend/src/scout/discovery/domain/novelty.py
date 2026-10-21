"""Novelty from measured OpenAlex counts (never guessed by a model)."""

from __future__ import annotations

import math
from collections.abc import Sequence


def novelty_score(count: int, saturation: int) -> float:
    """``1 - log10(count + 1) / log10(saturation + 1)`` clamped to [0, 1]; monotone non-increasing in ``count``."""
    if count < 0 or saturation < 1:
        raise ValueError("count must be >= 0 and saturation >= 1")
    return max(0.0, min(1.0, 1 - math.log10(count + 1) / math.log10(saturation + 1)))


def probe_queries(search_terms: Sequence[str], top_keyword: str | None) -> list[str]:
    """P1 = the cluster's search terms; P2 = P1 plus the strongest vault keyword (when there is one and it is new)."""
    terms = [t.strip() for t in search_terms if t.strip()]
    p1 = " ".join(dict.fromkeys(terms))
    out = [p1] if p1 else []
    if p1 and top_keyword and top_keyword.strip().lower() not in p1.lower():
        out.append(f"{p1} {top_keyword.strip()}")
    return out
