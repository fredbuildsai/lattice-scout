"""Profile computation, guards, regions and the vocabulary file (pure)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from scout.matching.adapters.vocabulary import load_vocabulary
from scout.matching.domain.agent_io import MethodMention
from scout.matching.domain.enums import CareerBand, Region
from scout.matching.domain.guards import clean_signal, copies_signal, leaks_identity
from scout.matching.domain.profile import (
    WorkIn,
    activity,
    career_band,
    compute_topics,
    is_eligible,
    method_works,
    methods_hash,
    weigh_methods,
)
from scout.matching.domain.regions import region_of

NOW = datetime(2026, 10, 22, tzinfo=UTC)
VOCAB = load_vocabulary(Path(__file__).resolve().parents[4] / "config" / "methods.yaml")


def work(
    ref: str, year: int | None, cited: int = 0, topics=(("t1", "T1"),), abstract: str | None = "x"
) -> WorkIn:
    return WorkIn(ref, f"Title {ref}", abstract, year, cited, tuple(topics))


def test_vocabulary_file_has_44_unique_methods() -> None:
    assert (
        len(VOCAB.terms) == 44
        and VOCAB.label("lca") == "Life-cycle assessment"
        and VOCAB.label("zzz") is None
    )


def test_topics_are_normalised_recency_weighted_and_bounded() -> None:
    works = [
        work("work:W1", 2026, 10, [("a", "A"), ("b", "B")]),
        work("work:W2", 2025, 0, [("a", "A")]),
        work("work:W3", 2024, 5, [("c", "C")]),
        work("work:W4", 2010, 99, [("old", "Old")]),  # outside the window
    ]
    topics = compute_topics(works, NOW)
    assert topics[0].id == "a" and topics[0].weight == 1.0
    assert {t.id for t in topics} == {"a", "b", "c"}
    assert all(0 < t.weight <= 1 for t in topics)


def test_topics_fall_back_to_all_works_when_the_window_is_thin() -> None:
    topics = compute_topics([work("work:W1", 2012, 1, [("x", "X")])], NOW)
    assert [t.id for t in topics] == ["x"]
    assert compute_topics([], NOW) == []


def test_method_works_takes_newest_with_abstract() -> None:
    ws = [work(f"work:W{i}", 2000 + i, abstract=None if i == 29 else "a") for i in range(30)]
    got = method_works(ws)
    assert len(got) == 20 and got[0].ref == "work:W28" and all(w.abstract for w in got)


def test_weigh_methods_drops_unknown_ids_and_refs_and_splits_sources() -> None:
    refs = {"work:W1": "abstract", "work:W2": "abstract", "insight:i1": "vault"}
    mentions = [
        MethodMention(method_id="lca", source_refs=["work:W1", "work:W2", "ghost"], confidence="high"),
        MethodMention(method_id="ftir", source_refs=["insight:i1"], confidence="medium"),
        MethodMention(method_id="nope", source_refs=["work:W1"], confidence="high"),
        MethodMention(method_id="tea", source_refs=["ghost"], confidence="high"),
        MethodMention(method_id=None, new_label="new thing", source_refs=["work:W1"], confidence="medium"),
    ]
    w = weigh_methods(mentions, VOCAB, refs)
    assert w.abstract == {"lca": 1.0} and w.vault == {"ftir": 0.25}
    assert w.proposals == [("new thing", ("work:W1",))]
    assert w.effective(use_vault=False) == {"lca": 1.0}
    assert set(w.effective(use_vault=True)) == {"lca", "ftir"}


def test_bands_activity_and_eligibility() -> None:
    assert career_band([work("w", 2022)], NOW) is CareerBand.EARLY
    assert career_band([work("w", 2016)], NOW) is CareerBand.MID
    assert career_band([work("w", 2005)], NOW) is CareerBand.SENIOR
    assert career_band([work("w", None)], NOW) is CareerBand.UNKNOWN
    assert activity([work("a", 2026), work("b", 2025), work("c", 2024), work("d", 2023)], NOW) == 1.0
    assert activity([work("a", 2020)], NOW) == 0.0
    topics = compute_topics([work("a", 2026, 1, [("a", "A"), ("b", "B"), ("c", "C")])], NOW)
    assert is_eligible(True, True, topics, {"lca": 1.0})
    assert not is_eligible(False, True, topics, {"lca": 1.0})
    assert not is_eligible(True, False, topics, {"lca": 1.0})
    assert not is_eligible(True, True, topics, {})


def test_methods_hash_changes_only_with_its_inputs() -> None:
    a = methods_hash([("work:W1", "t1"), ("work:W2", "t2")], "1", "1.0.0")
    assert a == methods_hash([("work:W2", "t2"), ("work:W1", "t1")], "1", "1.0.0")
    assert a != methods_hash([("work:W1", "t1b"), ("work:W2", "t2")], "1", "1.0.0")
    assert a != methods_hash([("work:W1", "t1"), ("work:W2", "t2")], "2", "1.0.0")
    assert a != methods_hash([("work:W1", "t1"), ("work:W2", "t2")], "1", "1.0.1")


def test_guards_catch_identity_leaks_and_copied_signals() -> None:
    titles = ["Lignin based polyurethane foams for seating applications in vehicles"]
    assert leaks_identity("She published in 2021 on foams", titles)
    assert leaks_identity("see https://x.org", titles) and leaks_identity("mail a.b@example.edu", titles)
    assert leaks_identity("lignin based polyurethane foams for seating applications", titles)
    assert not leaks_identity("Brings foaming and mechanical testing know-how", titles)
    sig = "we measured compression set of forty eight foams across four lignin types in the spring"
    assert copies_signal("we measured compression set of forty eight foams across", [sig])
    assert not copies_signal("compression data on many foams", [sig])
    assert clean_signal("call +49 170 1234567 or a@b.edu http://x.y ok") == "call or ok"


def test_region_mapping() -> None:
    assert region_of("DE") is Region.EUROPE and region_of("br") is Region.SOUTH_AMERICA
    assert region_of("CA") is Region.NORTH_AMERICA and region_of(None) is Region.UNKNOWN
    assert region_of("ZZ") is Region.UNKNOWN
