"""External suggestions, pure domain: co-authors, candidates (PI only, exclusions, layer precedence), lookup pool,
topic weights, activity, scoring, ranking and the suggestion state."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from scout.matching.domain.external import (
    AuthorRef,
    AuthorTopic,
    Bridge,
    ExternalAuthor,
    ExternalLayer,
    ExternalSuggestion,
    InvalidSuggestionState,
    RawCandidate,
    ScoredExternal,
    SuggestionState,
    WorkAuthors,
    author_activity,
    author_topic_weights,
    collect_candidates,
    lookup_pool,
    rank_external,
    recent_since,
    recent_works_count,
    score_external,
    top_coauthors,
    topical_pages,
)

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
NET, FIELD = ExternalLayer.IN_NETWORK, ExternalLayer.IN_FIELD


def ref(i: str) -> AuthorRef:
    return AuthorRef(i, f"Name {i}")


def work(*ids: str, last: str | None = None) -> WorkAuthors:
    return WorkAuthors(tuple(ref(i) for i in ids), ref(last) if last else None)


def author(
    i: str, topics: dict[str, int], years: dict[int, int] | None = None, **kw: object
) -> ExternalAuthor:
    return ExternalAuthor(
        id=i, name=f"Name {i}", topics=tuple(AuthorTopic(t, f"Topic {t}", c) for t, c in topics.items()),
        works_by_year=years if years is not None else {2026: 2, 2025: 2}, **kw,  # type: ignore[arg-type]
    )  # fmt: skip


def test_top_coauthors_counts_shared_works_skips_me_and_breaks_ties_by_id() -> None:
    works = [work("ME", "B", "C"), work("ME", "C", "C"), work("ME2", "C", "D"), work("ME", "B")]
    got = top_coauthors(works, {"ME", "ME2"}, n=2)
    assert got == [Bridge("C", "Name C", 3), Bridge("B", "Name B", 2)]


def test_collect_candidates_pi_only_exclusions_and_network_precedence() -> None:
    b1, b2 = Bridge("B1", "Bea One", 3), Bridge("B2", "Ben Two", 1)
    bridged = [
        (b1, [work("B1", "X", last="P1"), work("B1", "P2", last="ME")]),
        (b2, [work("B2", "Q", last="P1"), work("B2", last="P3")]),
    ]
    topical = [work("Z", last="P1"), work("Z", last="F1"), work("Y", last="F1"), work("Z", last="MEMBER")]
    got = collect_candidates(bridged, topical, exclude={"ME", "MEMBER"}, pi_only=True)
    assert [(c.id, c.layer, c.vias, c.hits) for c in got] == [
        ("P1", NET, ["Bea One", "Ben Two"], 3),  # found three times; network wins, both bridges kept
        ("P3", NET, ["Ben Two"], 1),
        ("F1", FIELD, [], 2),
    ]
    assert all(c.id not in {"X", "P2", "Q", "Z", "Y"} for c in got)  # PI only: middle authors never count


def test_collect_candidates_network_overrides_an_earlier_field_hit() -> None:
    # A topical hit that is also bridged must end up in_network with the bridge as via.
    got = collect_candidates(
        [(Bridge("B", "Bea", 1), [work(last="P")])], [work(last="P")], set(), pi_only=True
    )
    assert [(c.layer, c.vias) for c in got] == [(NET, ["Bea"])]


def test_collect_candidates_without_pi_only_takes_every_author() -> None:
    got = collect_candidates([], [work("A", "B", last="B")], exclude={"A"}, pi_only=False)
    assert [c.id for c in got] == ["B"]


def test_lookup_pool_reserves_sixty_percent_for_the_network_and_fills_up() -> None:
    net = [RawCandidate(f"N{i:02}", "", NET) for i in range(40)]
    fld = [RawCandidate(f"F{i:02}", "", FIELD) for i in range(40)]
    pool = lookup_pool([*net, *fld], max_lookups=50, network_share=0.6)
    assert len(pool) == 50
    assert sum(c.layer is NET for c in pool) == 30 and sum(c.layer is FIELD for c in pool) == 20
    few_field = lookup_pool([*net, *fld[:5]], max_lookups=50, network_share=0.6)
    assert len(few_field) == 45 and sum(c.layer is NET for c in few_field) == 40  # unused slots go back


def test_topic_weights_activity_and_recent_works() -> None:
    a = author("A", {"T1": 3, "T2": 1, "T0": 0}, years={2026: 1, 2025: 1, 2024: 4, 2023: 9})
    assert author_topic_weights(a) == {"T1": 0.75, "T2": 0.25}
    assert recent_works_count(a, 2026) == 6
    assert author_activity(a, 2026) == 1.0
    assert author_activity(author("B", {}, years={2026: 1}), 2026) == pytest.approx(1 / 3)
    assert author_topic_weights(author("C", {})) == {}


def test_score_external_formula_and_thresholds() -> None:
    mine = {"T1": 1.0, "T2": 0.5}
    theirs = {"T1": 0.5, "T3": 0.5}
    kept = score_external(mine, 1.0, theirs, 0.0, alpha=1.0, min_topic=0.1, min_score=0.11)
    assert kept is not None
    score, t = kept
    assert t == pytest.approx(0.5)  # overlap coefficient: min(1, 0.5) over the lighter side (sum 1)
    assert score == pytest.approx(0.5 * 0.5)  # T * (0.5 + 0.5 * min(activity)) with min activity 0
    assert score_external(mine, 1.0, {"T9": 1.0}, 1.0) is None  # no overlap
    assert score_external(mine, 1.0, theirs, 0.0, min_score=0.3) is None
    assert score_external(mine, 1.0, theirs, 1.0, min_topic=0.6) is None


def test_rank_external_network_first_then_score_with_threshold_and_size() -> None:
    mine = {"T1": 1.0, "T2": 1.0}
    raws = [
        RawCandidate("N1", "Raw N1", NET, ["Bea"]),
        RawCandidate("F1", "", FIELD),
        RawCandidate("F2", "", FIELD),
        RawCandidate("F3", "", FIELD),  # no topic in common: dropped
        RawCandidate("MISSING", "", FIELD),  # lookup returned nothing: skipped
    ]
    authors = {
        "N1": author("N1", {"T1": 1, "T5": 3}, institution="Uni N", h_index=7, citations=99, works_count=12),
        "F1": author("F1", {"T1": 1, "T2": 1}),
        "F2": author("F2", {"T2": 1, "T5": 1}),
        "F3": author("F3", {"T9": 1}),
    }
    got = rank_external(
        mine, 1.0, raws, authors, current_year=2026, size=10, my_topic_names={"T2": "Mine T2"}
    )
    assert [(s.author.id, s.layer) for s in got] == [("N1", NET), ("F1", FIELD), ("F2", FIELD)]
    n1 = got[0]
    assert n1.vias == ("Bea",) and n1.score == pytest.approx(0.25) and n1.recent_works == 4
    assert n1.author.institution == "Uni N" and n1.author.h_index == 7
    assert got[1].score == pytest.approx(1.0) and got[1].shared_topics == ("Topic T1", "Topic T2")
    assert rank_external(mine, 1.0, raws, authors, current_year=2026, size=1)[0].author.id == "N1"


def test_topical_pages_move_deeper_and_wrap() -> None:
    assert topical_pages(0) == [1, 2]
    assert topical_pages(45) == [1, 4]
    assert topical_pages(20 * 19) == [1, 2]  # wraps after page 20


def test_recent_since_is_three_years_back() -> None:
    assert recent_since(NOW) == "2023-11-17"
    assert recent_since(datetime(2028, 2, 29, tzinfo=UTC)) == "2025-02-28"


def test_suggestion_state_changes() -> None:
    scored = ScoredExternal(author("A", {"T1": 1}), FIELD, (), 0.5, 0.5, ("T1",), 4)
    s = ExternalSuggestion.from_scored(scored, id=UUID(int=1), account_id=UUID(int=2), now=NOW)
    assert s.state.value == "active" and s.formula == "ext1" and s.openalex_id == "A"
    later = datetime(2026, 11, 18, tzinfo=UTC)
    s.set_state(SuggestionState.SAVED, later)
    assert s.state is SuggestionState.SAVED and s.updated_at == later
    with pytest.raises(InvalidSuggestionState):
        s.set_state("boom", later)  # type: ignore[arg-type]
