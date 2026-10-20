"""M1 task 4 profile domain: names, grouping (fixtures + properties), researcher claim, work inclusion, topics.
Design: docs/design/M1-identity-profile-claim.md section 3, decisions 30-31."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.profile.application.mapping import to_snapshot
from scout.profile.domain.grouping import AuthorSnapshot, RecordBasis, TopicWeight, group_candidates
from scout.profile.domain.names import name_tokens, names_compatible
from scout.profile.domain.researcher import ConflictingOrcids, ImportStatus, Researcher, VerifiedLevel
from scout.profile.domain.topics import compute_topics
from scout.profile.domain.work import ClaimedWork, UncertainReason, initial_inclusion
from scout.shared import AccountId, ValidationFailed

ROOT = Path(__file__).resolve().parents[4] / "tests/fixtures/openalex"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
ACCT = AccountId(UUID(int=1))


def test_name_tokens_and_compatibility() -> None:
    assert name_tokens("E. Johan Foster (1620910)") == ("e", "johan", "foster")
    assert names_compatible("Earl Johan Foster", "Foster E. Johan")
    assert names_compatible("Johan Foster", "E. Johan Foster")
    assert names_compatible("José Álvarez", "jose alvarez")
    assert not names_compatible("Foster", "Johan Foster")  # one token
    assert not names_compatible("Johan Foster", "Johan Fisher")
    assert not names_compatible("J. F.", "Johan Foster")  # no strong match


@given(st.text(max_size=20), st.text(max_size=20))
def test_names_compatible_symmetric(a: str, b: str) -> None:
    assert names_compatible(a, b) == names_compatible(b, a)


def _foster_snapshots() -> list[AuthorSnapshot]:
    lit = FixtureLiterature(ROOT)
    hits = asyncio.run(lit.search_authors("Johan Foster"))
    return [to_snapshot(h) for h in hits]


def test_grouping_on_foster_fixture() -> None:
    groups = group_candidates(_foster_snapshots(), affiliation_hint="British Columbia")
    g = groups[0]
    assert g.primary.id == "A5059588273" and g.includes_records == 2 and g.works_count == 160
    assert {r.snapshot.id: r.basis for r in g.records} == {
        "A5059588273": RecordBasis.PRIMARY,
        "A5140167166": RecordBasis.ORCID,
    }
    assert {r.snapshot.id for r in g.possible} == {"A5138740368", "A5158972402"}
    assert {x.primary.id for x in groups[1:]} == {"A5003854624", "A5030450974", "A5085584773"}


def test_grouping_is_order_invariant_and_partitions_ids() -> None:
    snaps = _foster_snapshots()
    base = group_candidates(snaps)
    shuffled = snaps[:]
    random.Random(1).shuffle(shuffled)
    assert [g.primary.id for g in group_candidates(shuffled)] == [g.primary.id for g in base]
    seen = [r.snapshot.id for g in base for r in (*g.records, *g.possible)]
    assert sorted(seen) == sorted({s.id for s in snaps})


snap_st = st.builds(
    AuthorSnapshot,
    id=st.sampled_from([f"A{i}" for i in range(8)]),
    display_name=st.sampled_from(["Ada Lovelace", "A. Lovelace", "Ada King", "Bob Stone", "B. Stone"]),
    orcid=st.sampled_from([None, None, "0000-0001", "0000-0002"]),
    works_count=st.integers(0, 50),
    institutions=st.sampled_from([(), ("MIT",), ("MIT", "UBC"), ("UBC",)]),
)


@given(st.lists(snap_st, max_size=10, unique_by=lambda s: s.id))
@settings(max_examples=100)
def test_grouping_invariants(snaps: list[AuthorSnapshot]) -> None:
    groups = group_candidates(snaps)
    seen = [r.snapshot.id for g in groups for r in (*g.records, *g.possible)]
    assert sorted(seen) == sorted(s.id for s in snaps)  # each id exactly once
    for g in groups:
        orcids = {r.snapshot.orcid for r in g.records}
        assert len(orcids) == 1 or len(g.records) == 1
        assert g.works_count == sum(r.snapshot.works_count for r in g.records)
        for p in g.possible:
            assert p.snapshot.orcid is None and p.basis == RecordBasis.INSTITUTION_NAME
            assert {i.casefold() for i in p.snapshot.institutions} & {i.casefold() for i in g.institutions}
    assert [g.primary.id for g in groups] == [g.primary.id for g in group_candidates(list(reversed(snaps)))]


def _s(
    id: str,
    name: str = "Ada Lovelace",
    orcid: str | None = None,
    inst: tuple[str, ...] = ("MIT",),
    n: int = 5,
) -> AuthorSnapshot:
    return AuthorSnapshot(id=id, display_name=name, orcid=orcid, works_count=n, institutions=inst)


def test_claim_builds_records_and_verification() -> None:
    r = Researcher.claim(
        UUID(int=9), ACCT, [_s("A1", orcid="0000-1"), _s("A2", orcid="0000-1", n=1)], "A1", NOW
    )
    assert r.author_ids == ("A1", "A2") and [x.merged for x in r.openalex_ids] == [False, True]
    assert r.import_generation == 1 and r.import_status == ImportStatus.QUEUED
    assert r.verified_level == VerifiedLevel.ORCID_MATCHED
    assert [type(e).__name__ for e in r.pull_events()] == ["ProfileClaimed"]
    weak = Researcher.claim(
        UUID(int=10), ACCT, [_s("A1", orcid="0000-1"), _s("A3", "A. Lovelace")], "A1", NOW
    )
    assert (
        weak.verified_level == VerifiedLevel.SELF_CLAIMED
        and weak.openalex_ids[1].basis == RecordBasis.INSTITUTION_NAME
    )
    weak.recompute_verification(contested=True)
    assert weak.verified_level.value == VerifiedLevel.CONTESTED.value


def test_claim_rejects_conflicting_orcids_and_bad_primary() -> None:
    with pytest.raises(ConflictingOrcids):
        Researcher.claim(UUID(int=9), ACCT, [_s("A1", orcid="0000-1"), _s("A2", orcid="0000-2")], "A1", NOW)
    with pytest.raises(ValidationFailed):
        Researcher.claim(UUID(int=9), ACCT, [_s("A1")], "A9", NOW)


def test_reclaim_bumps_generation_and_stale_generations_are_ignored() -> None:
    r = Researcher.claim(UUID(int=9), ACCT, [_s("A1")], "A1", NOW)
    assert r.same_claim(["A1"], "A1") and not r.same_claim(["A1", "A2"], "A1")
    r.reclaim([_s("A1"), _s("A2")], "A1", NOW)
    assert r.import_generation == 2
    assert (
        not r.start_import(1, NOW)
        and not r.fail_import(1, "x", NOW)
        and not r.finish_import(1, [], None, None, NOW)
    )
    assert r.start_import(2, NOW) and r.import_status == ImportStatus.IMPORTING
    assert r.finish_import(2, [], "MIT", "US", NOW) and r.import_status.value == ImportStatus.DONE.value


def test_initial_inclusion_rules() -> None:
    v = ["Earl Johan Foster"]
    assert initial_inclusion("article", ["Foster E. Johan"], v) is None
    assert initial_inclusion("review", [], v) is None  # truncated authorships never penalise
    assert initial_inclusion("preprint", ["Earl Johan Foster"], v) is UncertainReason.NON_ARTICLE_TYPE
    assert initial_inclusion("article", ["Someone Else"], v) is UncertainReason.NAME_MISMATCH


def _w(id: str, topics: tuple[TopicWeight, ...], included: bool = True) -> ClaimedWork:
    return ClaimedWork(
        id=UUID(int=int(id[1:])),
        researcher_id=UUID(int=1),
        openalex_work_id=id,
        title=id,
        topics=topics,
        included=included,
    )


def test_user_toggle_survives_refresh_and_topics_ignore_excluded() -> None:
    w = _w("W1", ())
    w.toggle(False)
    fresh = _w("W1", ())
    w.refresh(fresh, "A2", None)
    assert not w.included and w.source_author_ids == {"A2"}
    t1, t2 = TopicWeight(id="T1", weight=1.0), TopicWeight(id="T2", weight=0.0)
    topics = compute_topics([_w("W2", (t1, t2)), _w("W3", (t1,)), _w("W4", (t2,), included=False)])
    assert (
        [t.topic_id for t in topics] == ["T1", "T2"] and topics[0].weight == 1.0 and topics[1].weight == 0.5
    )
    assert compute_topics([_w("W5", (t1,), included=False)]) == []
