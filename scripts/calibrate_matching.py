#!/usr/bin/env python3
"""Calibrate the matching score (F-39): topic-similarity measure, ``MATCH_MIN_SCORE`` and alpha/beta.

No network, no database, deterministic (seeded). Inputs:

* the recorded profiles of the public demo pair Foster/Moreira (computed from ``tests/fixtures/openalex`` through the
  same code as ``make_matching_fixtures.py``; the only real anchor, and only public OpenAlex records);
* a synthetic pool of 36 ``example.edu`` researchers built from the controlled vocabularies: topics from the recorded
  OpenAlex topic list (``tests/fixtures/openalex/topics.json``, grouped by subfield into 6 themes) and methods from
  ``config/methods.yaml`` (split into 4 families). Each profile mimics a real one: a primary theme (9 of the theme's 16 topics, weights
  decaying from 1) plus a long tail of 12 off-theme topics at weight 0.12-0.25 (a real 25-topic profile has exactly
  this noisy tail, which is why weighted Jaccard is structurally small).

Pair labels are **by construction** (never by looking at the score):

* ``good``     same theme and different method families (nothing shared): related topics, different methods;
* ``similar``  same theme and same method group (4 shared core methods, 1 own method each): too similar;
* ``unrelated`` different themes and nearly disjoint methods (M < 0.2): the hardest unrelated case.

For each topic-similarity measure the script prints the T, M and score distribution per label, the margin between the
worst good pair and the best bad pair, and what the demo pair gets. The synthetic pool defines the *relative*
ordering of the labels; the absolute level of a real pair is anchored only by the demo pair, so the final tuning
still needs pilot feedback (BACKLOG F-39).

Usage: ``cd backend && uv run python ../scripts/calibrate_matching.py``
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import math
import random
import statistics
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from itertools import combinations
from uuid import UUID
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from scout.matching.adapters.vocabulary import load_vocabulary  # noqa: E402
from scout.shared import AccountId  # noqa: E402
from scout.matching.domain.scoring import (  # noqa: E402
    cosine,
    overlap_coefficient,
    weighted_jaccard,
)

OPENALEX = ROOT / "tests" / "fixtures" / "openalex"
OPENALEX_TOPICS = OPENALEX / "topics.json"
VOCABULARY = ROOT / "config" / "methods.yaml"
SEED = 20261021
THEMES, PER_THEME, N_FAMILIES = 6, 6, 4
THEME_SIZE, PRIMARY, TAIL = 16, 9, 12
ALPHA, BETA = 1.0, 1.5

Vec = Mapping[str, float]
MEASURES: dict[str, Callable[[Vec, Vec], float]] = {
    "wjaccard (c1)": weighted_jaccard,
    "overlap_coeff": overlap_coefficient,
    "cosine": cosine,
}


@dataclass(frozen=True)
class Person:
    """One profile of the pool (or the demo pair)."""

    name: str
    theme: int
    group: tuple[int, int]  # (method family, group id inside the family)
    topics: dict[str, float]
    methods: dict[str, float]
    activity: float


def build_pool() -> list[Person]:
    """36 deterministic synthetic profiles from the controlled vocabularies."""
    rng = random.Random(SEED)
    topics = json.loads(OPENALEX_TOPICS.read_text())
    ordered = sorted(topics, key=lambda t: (topics[t]["subfield"], t))
    size = THEME_SIZE
    blocks = [ordered[i * size : (i + 1) * size] for i in range(THEMES)]
    method_ids = [t.id for t in load_vocabulary(VOCABULARY).terms]
    fam = len(method_ids) // N_FAMILIES
    families = [method_ids[i * fam : (i + 1) * fam] for i in range(N_FAMILIES)]
    # inside a family the core (4 methods) is shared by the whole group; a person adds one own method.
    pool: list[Person] = []
    for theme in range(THEMES):
        for k in range(PER_THEME):
            # two method families per theme (k even / odd): same family = same core, different family = disjoint
            family = (theme + k % 2) % N_FAMILIES
            core, rest = families[family][:4], families[family][4:]
            own = rest[(theme * PER_THEME + k) % len(rest)]
            methods = {m: w for m, w in zip([*core, own], [1.0, 0.75, 0.75, 0.5, 0.5], strict=True)}
            prim = rng.sample(blocks[theme], PRIMARY)
            weights = {t: round(0.8**i * rng.uniform(0.85, 1.0), 4) for i, t in enumerate(prim)}
            other = [t for t in ordered if t not in blocks[theme]]
            weights |= {t: round(rng.uniform(0.12, 0.25), 4) for t in rng.sample(other, TAIL)}
            pool.append(
                Person(f"synth-{theme}-{k}@example.edu", theme, (family, 0), weights, methods, rng.choice([1 / 3, 2 / 3, 1.0]))
            )
    return pool


def label(a: Person, b: Person, m: float) -> str | None:
    """Label by construction (see the module docstring); ``None`` for pairs that fit no label."""
    if a.theme == b.theme:
        if a.group[0] != b.group[0]:
            return "good"
        return "similar"
    return "unrelated" if m < 0.2 else None


def third_subject() -> tuple[str, str] | None:
    """``(name, author id)`` of the third public test subject when his OpenAlex record has been recorded (B7)."""
    for f in sorted((OPENALEX / "authors").glob("A*.json")):
        name = json.loads(f.read_text()).get("display_name", "")
        if "chesneau" in name.lower():
            return name, f.stem
    return None


def recorded_people() -> dict[str, Person]:
    """Foster, Moreira and, when recorded, the third subject, as profiled from the OpenAlex fixtures (fixed clock)."""
    import make_matching_fixtures as fx

    third = third_subject()
    extra = {AccountId(UUID(int=0xF2)): (third[0], [third[1]])} if third else None
    logging.disable(logging.CRITICAL)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        run = asyncio.run(fx.run_pair(extra))
    names = {fx.FOSTER: "Foster", fx.MOREIRA: "Moreira", AccountId(UUID(int=0xF2)): "Chesneau"}
    out = {}
    for acc, p in run.profiles.items():
        n = names[acc]
        out[n] = Person(n, -1, (-1, -1), {t.id: t.weight for t in p.topics}, dict(p.methods), p.activity)
    return out


def score(sim: Callable[[Vec, Vec], float], a: Person, b: Person) -> tuple[float, float, float]:
    """``(T, M, score)`` with the given topic measure; M is always weighted Jaccard."""
    t, m = sim(a.topics, b.topics), weighted_jaccard(a.methods, b.methods)
    act = 0.5 + 0.5 * min(a.activity, b.activity)
    return t, m, (t**ALPHA) * ((1 - m) ** BETA) * act


def quantiles(xs: list[float]) -> str:
    """min / p10 / median / p90 / max."""
    s = sorted(xs)
    q = lambda p: s[min(len(s) - 1, int(p * (len(s) - 1) + 0.5))]  # noqa: E731
    return " ".join(f"{v:.3f}" for v in (s[0], q(0.1), statistics.median(s), q(0.9), s[-1]))


def rates(rows: list[float], thr: float) -> float:
    """Share of ``rows`` at or above ``thr``."""
    return sum(1 for r in rows if r >= thr) / len(rows) if rows else 0.0


def main() -> None:
    pool = build_pool()
    pairs = list(combinations(pool, 2))
    people = recorded_people()
    demo = (people["Foster"], people["Moreira"])
    print(f"pool: {len(pool)} synthetic profiles, {len(pairs)} pairs; alpha={ALPHA} beta={BETA}\n")
    for name, sim in MEASURES.items():
        by: dict[str, list[float]] = {"good": [], "similar": [], "unrelated": []}
        print(f"== {name}   (min p10 median p90 max)")
        raw: dict[str, list[tuple[float, float, float]]] = {k: [] for k in by}
        for a, b in pairs:
            t, m, sc = score(sim, a, b)
            lab = label(a, b, weighted_jaccard(a.methods, b.methods))
            if lab:
                raw[lab].append((t, m, sc))
                by[lab].append(sc)
        for lab, rows in raw.items():
            print(f"  {lab:<9} n={len(rows):<4} T {quantiles([r[0] for r in rows])} | M {quantiles([r[1] for r in rows])}"
                  f" | score {quantiles([r[2] for r in rows])}")
        bad = by["similar"] + by["unrelated"]
        print("  threshold -> share passing: good / similar / unrelated")
        for thr in (0.05, 0.08, 0.10, 0.11, 0.12, 0.15):
            print(f"    {thr:.2f}   {rates(by['good'], thr):4.0%} / {rates(by['similar'], thr):4.0%} / "
                  f"{rates(by['unrelated'], thr):4.0%}")
        # threshold that lets at most 5% of the bad pairs through, and what that costs the good pairs
        cands = sorted(set(round(x, 3) for x in bad))
        thr5 = next((c for c in cands if rates(bad, c) <= 0.05), cands[-1])
        print(f"  threshold with <=5% bad passing: {thr5:.3f} -> good passing {rates(by['good'], thr5):.0%}")
        t, m, sc = score(sim, *demo)
        print(f"  DEMO Foster/Moreira: T {t:.3f} M {m:.3f} score {sc:.3f} | passes that threshold: {sc >= thr5}"
              f" | bad pairs scoring >= demo: {rates(bad, sc):.0%}\n")
    third = third_subject()
    if third is None:
        print("Chesneau (third public test subject): NOT EVALUATED. His OpenAlex record is not recorded yet "
              "(blocker B7); his pairs with Foster and Moreira are not yet evaluated.")
    else:
        print(f"Third subject {third[0]} ({third[1]}), pairs with the demo pair:")
        for other in ("Foster", "Moreira"):
            for name, sim in MEASURES.items():
                t, m, sc = score(sim, people["Chesneau"], people[other])
                print(f"  Chesneau/{other:<8} {name:<14} T {t:.3f} M {m:.3f} score {sc:.3f}")


if __name__ == "__main__":
    main()
