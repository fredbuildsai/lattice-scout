"""Pure helpers: grouping, coverage, novelty, scoring, duplicate detection."""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scout.discovery.domain.coverage import (
    ClusterStats,
    candidate_scores,
    density,
    pick_candidates,
    tag_weight,
    user_coverage,
)
from scout.discovery.domain.grouping import GroupInput, group_works
from scout.discovery.domain.novelty import novelty_score, probe_queries
from scout.discovery.domain.scoring import (
    effort_value,
    fit_score,
    is_duplicate,
    jaccard,
    rank_score,
    readiness_score,
    title_tokens,
)

topics = st.sampled_from([f"T{i}" for i in range(8)])
subfields = st.sampled_from(["Biomaterials", "Polymers", "Chemistry", None])
work_inputs = st.lists(
    st.builds(
        GroupInput,
        work_id=st.integers(0, 10_000).map(lambda i: f"W{i}"),
        topic_ids=st.lists(topics, max_size=3, unique=True).map(tuple),
        subfield=subfields,
    ),
    max_size=120,
    unique_by=lambda w: w.work_id,
)


@settings(max_examples=80, deadline=None)
@given(work_inputs, st.integers(1, 6), st.integers(8, 40))
def test_every_work_is_in_exactly_one_cluster_and_grouping_is_deterministic(
    works: list[GroupInput], lo: int, hi: int
) -> None:
    clusters = group_works(works, lo, hi)
    placed = [w for c in clusters for w in c.work_ids]
    assert sorted(placed) == sorted(w.work_id for w in works)
    assert clusters == group_works(list(reversed(works)), lo, hi)  # input order does not matter
    labelled = [c for c in clusters if not c.is_misc]
    assert [c.label_key for c in labelled] == [f"k{i}" for i in range(1, len(labelled) + 1)]
    sizes = [c.size for c in labelled]
    assert sizes == sorted(sizes, reverse=True)
    assert all(not c.is_misc or c is clusters[-1] for c in clusters)  # misc is last


def test_small_groups_merge_by_subfield_and_leftovers_go_to_misc() -> None:
    works = [
        GroupInput(f"W{i}", (f"T{i}",), "Biomaterials") for i in range(4)
    ]  # four topics of one work each
    works += [GroupInput("W9", ("T9",), "Polymers"), GroupInput("W10", (), None)]
    clusters = group_works(works, min_size=3, max_size=50)
    keys = {c.key: c for c in clusters}
    assert keys["sub:Biomaterials"].size == 4
    assert keys["misc"].is_misc and set(keys["misc"].work_ids) == {"W9", "W10"}


def test_large_groups_split_by_secondary_topic() -> None:
    works = [GroupInput(f"W{i:03d}", ("T1", "T2" if i % 2 else "T3"), "X") for i in range(30)]
    clusters = group_works(works, 3, 20)
    assert {c.key for c in clusters} == {"topic:T1/T2", "topic:T1/T3"} and all(c.size == 15 for c in clusters)


def test_coverage_and_density() -> None:
    assert user_coverage(0, 0) == 0 and user_coverage(3, 2) == 1 and user_coverage(6, 9) == 1
    assert user_coverage(3, 0) == pytest.approx(0.5) and user_coverage(0, 1) == pytest.approx(0.25)
    assert density(5, 10) == 0.5 and density(5, 0) == 0
    assert tag_weight("core") == 1.0 and tag_weight("partial") == 0.5 and tag_weight("other") == 0.0


def test_candidates_prefer_covered_thin_clusters_and_fall_back_to_the_largest() -> None:
    cs = [
        ClusterStats("big", 100, "A", user_works=3, insight_weight=2),  # covered but dense
        ClusterStats("thin", 10, "A", user_works=3, insight_weight=2),  # covered and thin: best
        ClusterStats("adjacent", 20, "A", user_works=0, insight_weight=0),  # same subfield as a covered one
        ClusterStats("far", 20, "B", user_works=0, insight_weight=0),
        ClusterStats("misc", 5, None, 0, 0, is_misc=True),
    ]
    scores = candidate_scores(cs)
    assert "misc" not in scores and scores["far"] == 0 and scores["thin"] > scores["adjacent"] > 0
    keys, fallback = pick_candidates(cs, limit=2)
    assert keys == ["thin", "big"] or keys[0] == "thin"
    assert not fallback
    none = [
        ClusterStats("a", 30, "A", 0, 0),
        ClusterStats("b", 20, "B", 0, 0),
        ClusterStats("c", 10, "C", 0, 0),
        ClusterStats("d", 5, "D", 0, 0),
    ]
    keys, fallback = pick_candidates(none)
    assert fallback and keys == ["a", "b", "c"]


@given(st.integers(0, 10**7), st.integers(0, 10**7), st.integers(2, 10**6))
def test_novelty_is_monotone_and_bounded(a: int, b: int, saturation: int) -> None:
    lo, hi = sorted((a, b))
    assert 0 <= novelty_score(hi, saturation) <= novelty_score(lo, saturation) <= 1


def test_novelty_values_for_the_recorded_counts() -> None:
    assert novelty_score(0, 5000) == 1.0 and novelty_score(5000, 5000) == 0.0
    assert [round(novelty_score(c, 5000), 2) for c in (427, 595, 865, 1731)] == [0.29, 0.25, 0.21, 0.12]
    with pytest.raises(ValueError):
        novelty_score(-1, 5000)


def test_probe_queries() -> None:
    assert probe_queries(["lignin", "foam", "lignin"], "flame retardant") == [
        "lignin foam",
        "lignin foam flame retardant",
    ]
    assert probe_queries(["lignin foam"], "foam") == ["lignin foam"]
    assert probe_queries([], "x") == [] and probe_queries([" "], None) == []


@given(st.floats(0, 1), st.floats(0, 1), st.floats(0, 1), st.floats(0, 1))
def test_rank_is_in_range(fit: float, nov: float, ready: float, eff: float) -> None:
    assert 0 <= rank_score(fit, nov, ready, eff) <= 1.0 + 1e-9


def test_scoring_parts() -> None:
    assert fit_score({"T1": 1.0, "T2": 0.5}, ["T1", "T2"], 2) == pytest.approx(0.6 * 0.75 + 0.4)
    assert fit_score({}, [], 0) == 0
    assert readiness_score(1, 4) == 0.25 and readiness_score(0, 0) == 0
    assert [effort_value(x) for x in ("low", "Medium", "high", None, "x")] == [0.25, 0.5, 0.85, 0.5, 0.5]


def test_duplicates() -> None:
    a = "Fire-safe lignin polyurethane foams for upholstery"
    assert title_tokens("The use of lignin") == frozenset({"lignin"}) or "lignin" in title_tokens(
        "The use of lignin"
    )
    assert jaccard(frozenset(), frozenset()) == 0
    assert is_duplicate("Lignin polyurethane foams for upholstery fire safety", [a]) == 0
    assert is_duplicate("Photocatalytic PFAS regeneration of membranes", [a, "other thing"]) is None
