"""Deterministic ids derived inside a run (a re-run step must write the same rows).

Covers plan M3 task 3 (resumability) and DECISIONS 57; design note M3 section 1 ("ids.py").
"""

from __future__ import annotations

from uuid import UUID, uuid4

from hypothesis import given
from hypothesis import strategies as st

from scout.discovery.domain import ids

RUN_A = UUID(int=11)
RUN_B = UUID(int=12)


def test_namespace_is_the_documented_constant() -> None:
    assert UUID("3b7f6c1e-8a42-5d0b-9e17-c4d2a9f05e63") == ids.DISCOVERY_NS


def test_ids_are_stable_across_calls_and_versions_are_5() -> None:
    for make in (
        lambda: ids.corpus_work_id(RUN_A, "W1"),
        lambda: ids.cluster_id(RUN_A, "T10610"),
        lambda: ids.probe_id(RUN_A, "n1"),
        lambda: ids.opportunity_id(RUN_A, 0, 1),
        lambda: ids.claim_id(RUN_A, 2),
        lambda: ids.evidence_id(RUN_A, 3),
    ):
        assert make() == make() and make().version == 5


def test_the_same_key_in_another_run_gives_another_id() -> None:
    assert ids.corpus_work_id(RUN_A, "W1") != ids.corpus_work_id(RUN_B, "W1")
    assert ids.cluster_id(RUN_A, "k") != ids.cluster_id(RUN_B, "k")
    assert ids.probe_id(RUN_A, "n1") != ids.probe_id(RUN_B, "n1")
    assert ids.opportunity_id(RUN_A, 0, 0) != ids.opportunity_id(RUN_B, 0, 0)


def test_different_kinds_never_collide_for_the_same_text() -> None:
    same = {ids.corpus_work_id(RUN_A, "x"), ids.cluster_id(RUN_A, "x"), ids.probe_id(RUN_A, "x")}
    assert len(same) == 3


@given(b1=st.integers(0, 5), o1=st.integers(0, 9), b2=st.integers(0, 5), o2=st.integers(0, 9))
def test_opportunity_ids_are_injective_in_batch_and_ordinal(b1: int, o1: int, b2: int, o2: int) -> None:
    same = ids.opportunity_id(RUN_A, b1, o1) == ids.opportunity_id(RUN_A, b2, o2)
    assert same == ((b1, o1) == (b2, o2))


def test_children_ids_follow_their_parent() -> None:
    opp = ids.opportunity_id(RUN_A, 0, 0)
    claims = [ids.claim_id(opp, i) for i in range(5)]
    assert len(set(claims)) == 5
    assert ids.claim_id(opp, 0) != ids.claim_id(uuid4(), 0)
    assert len({ids.evidence_id(claims[0], i) for i in range(4)}) == 4
    assert ids.evidence_id(claims[0], 0) != ids.evidence_id(claims[1], 0)
