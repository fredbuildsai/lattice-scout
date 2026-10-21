"""Pure scoring: weighted Jaccard, complementarity formula c1 and the weekly cap (plan M5 task 2; ARCHITECTURE §6.3;
docs/design/M5-matching.md §4 steps 2-3 and invariant I8).

Assumed API, scout.matching.domain.scoring (all pure):
* ``weighted_jaccard(x: Mapping[str, float], y: Mapping[str, float]) -> float`` = sum(min) / sum(max) over the key
  union; 0.0 when either mapping is empty.
* ``score_pair(*, a_topics, b_topics, a_methods, b_methods, a_activity, b_activity, alpha=1.0, beta=1.5,
  min_score=0.08) -> ScoredPair | None`` where topics/methods are ``Mapping[str, float]`` (id -> weight) and
  the result has ``.score: float`` and ``.basis: ScoreBasis`` (topic_overlap, method_overlap, activity,
  alpha, beta, shared_topics, a_only, b_only as ids sorted and truncated to 5, formula == "c1").
  score = T**alpha * (1-M)**beta * (0.5 + 0.5*min(act_a, act_b)). Returns None when score < min_score or when
  either side has no method the other lacks.
* ``select_pairs(pairs: Sequence[tuple[AccountId, AccountId, float]], *, open_counts: Mapping[AccountId, int],
  cap: int) -> list[tuple[AccountId, AccountId, float]]``: sort by (-score, a, b), take greedily while both sides
  have open_counts[x] + already selected in this call < cap. ``open_counts`` = proposals created in the last 7 days.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from scout.matching.domain.scoring import score_pair, select_pairs, weighted_jaccard
from scout.shared import AccountId

PROFILE = settings(
    max_examples=100,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    database=None,
)
keys = st.sampled_from([f"k{i}" for i in range(8)])
weights = st.floats(min_value=0.01, max_value=1.0, allow_nan=False)
vectors = st.dictionaries(keys, weights, max_size=8)
nonempty = st.dictionaries(keys, weights, min_size=1, max_size=8)


def acc(n: int) -> AccountId:
    return AccountId(UUID(int=n))


# ------------------------------------------------------------------ weighted Jaccard


@PROFILE
@given(x=vectors, y=vectors)
def test_jaccard_symmetric_and_bounded(x, y) -> None:
    j = weighted_jaccard(x, y)
    assert j == pytest.approx(weighted_jaccard(y, x))
    assert 0.0 <= j <= 1.0


@PROFILE
@given(x=nonempty)
def test_jaccard_identity_is_one(x) -> None:
    assert weighted_jaccard(x, dict(x)) == pytest.approx(1.0)


@PROFILE
@given(x=vectors)
def test_jaccard_with_empty_is_zero(x) -> None:
    assert weighted_jaccard({}, x) == 0.0
    assert weighted_jaccard(x, {}) == 0.0


def test_jaccard_worked_examples() -> None:
    assert weighted_jaccard({"a": 1.0, "b": 0.5}, {"b": 1.0, "c": 0.5}) == pytest.approx(0.5 / 2.5)
    assert weighted_jaccard({"a": 1.0}, {"b": 1.0}) == 0.0


# ------------------------------------------------------------------ score_pair (formula c1)

A_TOPICS = {"t1": 1.0, "t2": 1.0}
B_TOPICS = {"t1": 1.0, "t3": 1.0}  # T = 1/3
A_METHODS = {"m1": 1.0, "m2": 1.0}
B_METHODS = {"m2": 1.0, "m3": 1.0}  # M = 1/3


def pair(**over):
    args = dict(
        a_topics=A_TOPICS,
        b_topics=B_TOPICS,
        a_methods=A_METHODS,
        b_methods=B_METHODS,
        a_activity=1.0,
        b_activity=0.5,
    )
    args.update(over)
    return score_pair(**args)  # type: ignore[arg-type]


def test_score_formula_c1_with_default_alpha_beta() -> None:
    r = pair()
    expected = (1 / 3) ** 1.0 * (1 - 1 / 3) ** 1.5 * (0.5 + 0.5 * 0.5)
    assert r is not None
    assert r.score == pytest.approx(expected)
    b = r.basis
    assert (b.topic_overlap, b.method_overlap) == (pytest.approx(1 / 3), pytest.approx(1 / 3))
    assert b.activity == pytest.approx(0.75)
    assert (b.alpha, b.beta, b.formula) == (1.0, 1.5, "c1")
    assert list(b.shared_topics) == ["t1"]
    assert list(b.a_only) == ["m1"] and list(b.b_only) == ["m3"]


def test_score_is_symmetric_in_the_pair() -> None:
    fwd = pair()
    rev = pair(
        a_topics=B_TOPICS,
        b_topics=A_TOPICS,
        a_methods=B_METHODS,
        b_methods=A_METHODS,
        a_activity=0.5,
        b_activity=1.0,
    )
    assert fwd is not None and rev is not None and fwd.score == pytest.approx(rev.score)


def test_activity_factor_uses_the_less_active_side() -> None:
    low, high = pair(a_activity=0.0, b_activity=1.0), pair(a_activity=1.0, b_activity=1.0)
    assert low is not None and high is not None
    assert low.basis.activity == pytest.approx(0.5) and high.basis.activity == pytest.approx(1.0)
    assert low.score == pytest.approx(high.score / 2)


def test_alpha_beta_are_configurable() -> None:
    r = pair(alpha=2.0, beta=1.0, min_score=0.0)
    assert r is not None
    assert r.score == pytest.approx((1 / 3) ** 2 * (2 / 3) * 0.75)
    assert (r.basis.alpha, r.basis.beta) == (2.0, 1.0)


def test_min_score_filter() -> None:
    assert pair(min_score=0.5) is None
    assert pair(min_score=0.0) is not None
    s = pair().score
    assert pair(min_score=s - 1e-9) is not None  # inclusive boundary
    assert pair(min_score=s + 1e-9) is None


def test_requires_complementary_methods_on_both_sides() -> None:
    # a's methods are a subset of b's: a has nothing b lacks, even though the score alone would pass.
    assert pair(a_methods={"m2": 1.0}, b_methods={"m2": 1.0, "m3": 1.0}, min_score=0.0) is None
    assert pair(a_methods={"m2": 1.0, "m3": 1.0}, b_methods={"m2": 1.0}, min_score=0.0) is None
    assert pair(a_methods=A_METHODS, b_methods=dict(A_METHODS), min_score=0.0) is None


def test_basis_lists_are_truncated_to_five() -> None:
    many_a = {f"a{i}": 1.0 for i in range(9)} | {"s": 1.0}
    many_b = {f"b{i}": 1.0 for i in range(9)} | {"s": 1.0}
    topics = {f"t{i}": 1.0 for i in range(9)}
    r = pair(a_topics=topics, b_topics=dict(topics), a_methods=many_a, b_methods=many_b, min_score=0.0)
    assert r is not None
    assert len(r.basis.shared_topics) == len(r.basis.a_only) == len(r.basis.b_only) == 5


@PROFILE
@given(at=vectors, bt=vectors, am=vectors, bm=vectors, aa=st.floats(0, 1), ba=st.floats(0, 1))
def test_i8_score_always_within_unit_interval(at, bt, am, bm, aa, ba) -> None:
    r = pair(
        a_topics=at, b_topics=bt, a_methods=am, b_methods=bm, a_activity=aa, b_activity=ba, min_score=0.0
    )
    if r is not None:
        assert 0.0 <= r.score <= 1.0


# ------------------------------------------------------------------ select_pairs

triples = st.lists(
    st.tuples(st.integers(1, 6), st.integers(1, 6), st.floats(0.0, 1.0)).filter(lambda t: t[0] < t[1]),
    max_size=15,
    unique_by=lambda t: (t[0], t[1]),
)
opens = st.dictionaries(st.integers(1, 6), st.integers(0, 4))


def to_pairs(raw):
    return [(acc(a), acc(b), s) for a, b, s in raw]


def test_select_pairs_greedy_example() -> None:
    pairs = [(acc(1), acc(2), 0.9), (acc(1), acc(3), 0.8), (acc(4), acc(5), 0.5), (acc(3), acc(4), 0.4)]
    got = select_pairs(pairs, open_counts={}, cap=1)
    assert got == [(acc(1), acc(2), 0.9), (acc(4), acc(5), 0.5)]
    got2 = select_pairs(pairs, open_counts={}, cap=2)
    assert got2 == pairs  # nobody exceeds two


def test_select_pairs_respects_existing_open_counts_and_ties_break_by_ids() -> None:
    pairs = [(acc(2), acc(3), 0.5), (acc(1), acc(2), 0.5), (acc(1), acc(4), 0.5)]
    assert select_pairs(pairs, open_counts={acc(1): 1}, cap=2) == [
        (acc(1), acc(2), 0.5),
        (acc(2), acc(3), 0.5),
    ]
    assert select_pairs(pairs, open_counts={acc(2): 3}, cap=3) == [(acc(1), acc(4), 0.5)]
    assert select_pairs(pairs, open_counts={}, cap=0) == []


@PROFILE
@given(raw=triples, counts=opens, cap=st.integers(0, 4))
def test_select_pairs_never_exceeds_cap_is_maximal_and_deterministic(raw, counts, cap) -> None:
    pairs = to_pairs(raw)
    open_counts = {acc(k): v for k, v in counts.items()}
    chosen = select_pairs(pairs, open_counts=open_counts, cap=cap)
    assert set(chosen) <= set(pairs)
    used: Counter[Any] = Counter()
    for a, b, _ in chosen:
        used[a] += 1
        used[b] += 1
    for who, n in used.items():
        assert open_counts.get(who, 0) + n <= cap
    # maximal: every skipped pair has a saturated side
    for a, b, _ in set(pairs) - set(chosen):
        assert any(open_counts.get(x, 0) + used[x] >= cap for x in (a, b))
    # deterministic regardless of input order
    assert select_pairs(list(reversed(pairs)), open_counts=open_counts, cap=cap) == chosen
