"""Opportunity aggregate: evidence marking, claim lifecycle, scores, user-visible claims and the show rule.

Covers plan M3 task 1 (discovery domain) and task 3.7, ARCHITECTURE section 4.4 (opportunity shown only with
verified evidence); DECISIONS 59 (show rule), 60 (claim lifecycle) and 61 (opportunity statuses); design note M3
section 1 ("Domain invariants" 1, 2, 3, 4, 5).
"""

from __future__ import annotations

import copy
import math
from itertools import product

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st
from tests.unit.discovery._builders import claim, ev, opp, scores, showable_claims, uid

from scout.discovery.domain.enums import (
    CheckCode,
    ClaimSection,
    ClaimStatus,
    OppStatus,
    SetBy,
    SourceType,
)
from scout.discovery.domain.opportunity import USER_STATUSES, Claim, Evidence, Opportunity, Scores
from scout.shared import InvalidStateTransition, InvariantViolation

S = SourceType
CS = ClaimStatus
SEC = ClaimSection


# ================================================================================================================
# Evidence.mark_verified / mark_failed
# ================================================================================================================
COLUMN = {
    S.LITERATURE: "corpus_work_id",
    S.PROFILE: "corpus_work_id",
    S.VAULT: "chunk_id",
    S.COUNT: "probe_id",
}
COLUMNS = ("corpus_work_id", "chunk_id", "probe_id")


def bare(source: SourceType, column: str | None) -> Evidence:
    e = Evidence(id=uid("e"), ord=0, source_type=source, label="x", ref="x")
    if column:
        setattr(e, column, uid(f"target:{column}"))
    return e


@pytest.mark.parametrize(("source", "column"), list(product(SourceType, [*COLUMNS, None])))
def test_verified_needs_the_id_column_of_its_source_type(source: SourceType, column: str | None) -> None:
    e = bare(source, column)
    if column == COLUMN[source]:
        e.mark_verified("detail")
        assert e.verified is True and e.check is CheckCode.OK and e.check_detail == "detail"
    else:
        with pytest.raises(InvariantViolation):
            e.mark_verified()
        assert e.verified is None and e.check is None, "a refused verification must change nothing"


def test_mark_verified_keeps_a_missing_detail_empty() -> None:
    e = bare(S.COUNT, "probe_id")
    e.mark_verified()
    assert e.verified is True and e.check_detail is None


@pytest.mark.parametrize("code", [c for c in CheckCode if c is not CheckCode.OK])
def test_mark_failed_records_the_code(code: CheckCode) -> None:
    e = bare(S.LITERATURE, None)  # failing needs no target
    e.mark_failed(code, "why")
    assert e.verified is False and e.check is code and e.check_detail == "why"


def test_mark_failed_overrides_nothing_else() -> None:
    e = bare(S.VAULT, "chunk_id")
    e.mark_failed(CheckCode.SOURCE_DELETED)
    assert e.verified is False and e.chunk_id == uid("target:chunk_id") and e.check_detail is None


# ================================================================================================================
# Claim.set_status: forward only
# ================================================================================================================
ALLOWED_CLAIM = {
    (CS.PENDING, CS.UNVERIFIED), (CS.PENDING, CS.VERIFIED), (CS.PENDING, CS.CONTRADICTED),
    (CS.PENDING, CS.REJECTED_BY_CRITIC), (CS.VERIFIED, CS.REJECTED_BY_CRITIC),
}  # fmt: skip


@pytest.mark.parametrize(("old", "new"), list(product(CS, CS)))
def test_claim_status_moves_forward_only(old: ClaimStatus, new: ClaimStatus) -> None:
    c = claim(status=old)
    before = copy.deepcopy(c)
    if (old, new) in ALLOWED_CLAIM:
        c.set_status(new, "reason", SetBy.VERIFY_CRITIC)
        assert c.status is new and c.status_reason == "reason" and c.set_by is SetBy.VERIFY_CRITIC
    else:
        with pytest.raises(InvalidStateTransition):
            c.set_status(new, "reason", SetBy.VERIFY_CRITIC)
        assert c == before


def test_set_status_records_who_and_why_and_allows_no_reason() -> None:
    c = claim(status=CS.PENDING)
    c.set_status(CS.UNVERIFIED, CheckCode.QUOTE_NOT_FOUND.value, SetBy.VERIFY_CODE)
    assert (c.status_reason, c.set_by) == ("quote_not_found", SetBy.VERIFY_CODE)
    c2 = claim(status=CS.PENDING)
    c2.set_status(CS.VERIFIED, None, SetBy.VERIFY_CRITIC)
    assert c2.status_reason is None


def test_a_claim_keeps_its_evidence_when_rejected() -> None:
    c = claim(status=CS.VERIFIED, evidence=[ev(S.LITERATURE, "W1")])
    c.set_status(CS.REJECTED_BY_CRITIC, "critic_rejected", SetBy.VERIFY_CRITIC)
    assert len(c.evidence) == 1 and c.evidence[0].verified is True


# ================================================================================================================
# Scores
# ================================================================================================================
@pytest.mark.parametrize("value", [0.0, 0.5, 1.0])
def test_scores_accept_the_closed_unit_interval(value: float) -> None:
    s = Scores(fit=value, novelty=value, readiness=value, effort=value, rank=value)
    assert s.rank == value


@pytest.mark.parametrize("field", ["fit", "novelty", "readiness", "effort", "rank"])
@pytest.mark.parametrize("bad", [-0.001, 1.001, 2.0, -1.0, math.nan, math.inf, -math.inf])
def test_scores_reject_any_value_outside_0_1(field: str, bad: float) -> None:
    kw = {"fit": 0.5, "novelty": 0.5, "readiness": 0.5, "effort": 0.5, "rank": 0.5} | {field: bad}
    with pytest.raises(InvariantViolation):
        Scores(**kw)


def test_scores_are_immutable() -> None:
    with pytest.raises(AttributeError):
        scores().fit = 0.9  # type: ignore[misc]


# ================================================================================================================
# visible_claims
# ================================================================================================================
def test_visible_claims_keep_verified_claims_and_their_verified_evidence_only() -> None:
    good = ev(S.LITERATURE, "W1", n=0)
    mixed = claim(
        SEC.RATIONALE,
        CS.VERIFIED,
        [good, ev(S.LITERATURE, "W2", verified=False, n=1), ev(S.VAULT, "d1.c1", verified=None, n=2)],
        n=0,
    )
    others = [
        claim(SEC.RATIONALE, st_, [ev(S.LITERATURE, "W3", n=3)], n=i + 1)
        for i, st_ in enumerate(s for s in CS if s is not CS.VERIFIED)
    ]
    o = opp([others[0], mixed, *others[1:]])
    before = copy.deepcopy(o)
    [only] = o.visible_claims()
    assert only.id == mixed.id and [e.id for e in only.evidence] == [good.id]
    assert o == before, "reading the user view must not change the aggregate"


def test_visible_claims_returns_score_claims_and_keeps_the_order() -> None:
    o = opp(showable_claims())
    assert [c.section for c in o.visible_claims()] == [SEC.RATIONALE, SEC.SCORE]
    assert [len(c.evidence) for c in o.visible_claims()] == [2, 1]


def test_nothing_is_visible_without_verified_claims() -> None:
    o = opp(
        [claim(SEC.RATIONALE, s, [ev()], n=i) for i, s in enumerate(x for x in CS if x is not CS.VERIFIED)]
    )
    assert o.visible_claims() == []
    assert opp([]).visible_claims() == []


@st.composite
def random_opps(draw: st.DrawFn, *, bias_verified: bool = True) -> Opportunity:
    statuses = [CS.VERIFIED] * 4 + list(CS) if bias_verified else list(CS)
    claims: list[Claim] = []
    for i in range(draw(st.integers(0, 5))):
        evidence = [
            ev(draw(st.sampled_from(list(S))), draw(st.sampled_from(["a", "b", "c"])), verified=draw(st.sampled_from([True, True, False, None])), n=j)
            for j in range(draw(st.integers(0, 4)))
        ]  # fmt: skip
        claims.append(claim(draw(st.sampled_from(list(SEC))), draw(st.sampled_from(statuses)), evidence, n=i))
    return opp(claims, with_scores=draw(st.booleans()), status=draw(st.sampled_from(list(OppStatus))))


@settings(max_examples=300, deadline=None)
@given(random_opps())
def test_visible_claims_never_contains_anything_unverified(o: Opportunity) -> None:
    before = copy.deepcopy(o)
    visible = o.visible_claims()
    assert [c.id for c in visible] == [c.id for c in o.claims if c.status is CS.VERIFIED]
    for v in visible:
        assert v.status is CS.VERIFIED
        assert all(e.verified is True for e in v.evidence)
        original = next(c for c in o.claims if c.id == v.id)
        assert [e.id for e in v.evidence] == [e.id for e in original.evidence if e.verified is True]
    assert o == before


# ================================================================================================================
# show_decision: the truth table
# ================================================================================================================
def rat(
    *evs: Evidence, status: ClaimStatus = CS.VERIFIED, section: ClaimSection = SEC.RATIONALE, n: int = 0
) -> Claim:
    return claim(section, status, list(evs), n=n)


def score_claim(*evs: Evidence, status: ClaimStatus = CS.VERIFIED, n: int = 9) -> Claim:
    return claim(SEC.SCORE, status, list(evs) or [ev(S.COUNT, "n1")], n=n)


NOVELTY = score_claim()
LIT1, LIT2 = ev(S.LITERATURE, "W1", n=0), ev(S.LITERATURE, "W2", n=1)
VAULT1, PROF1, PROF2 = ev(S.VAULT, "d1.c1", n=2), ev(S.PROFILE, "W7", n=3), ev(S.PROFILE, "W8", n=4)
COUNT1 = ev(S.COUNT, "n1", n=5)

# name -> (claims, expected)
TRUE_CASES = {
    "literature + vault on one rationale": [rat(LIT1, VAULT1), NOVELTY],
    "two literature works on two claims": [rat(LIT1, n=0), rat(LIT2, n=1), NOVELTY],
    "an objection claim counts": [rat(LIT1, n=0), rat(LIT2, section=SEC.OBJECTION, n=1), NOVELTY],
    "literature + profile": [rat(LIT1, PROF1), NOVELTY],
    "literature + count evidence on a rationale": [rat(LIT1, COUNT1), NOVELTY],
    "more than two": [rat(LIT1, LIT2, VAULT1, PROF1), NOVELTY],
}


@pytest.mark.parametrize("name", list(TRUE_CASES))
def test_shown(name: str) -> None:
    ok, _ = opp(copy.deepcopy(TRUE_CASES[name])).show_decision()
    assert ok is True


def ev_u(source: SourceType, ref: str, verified: bool | None, n: int) -> Evidence:
    return ev(source, ref, verified=verified, n=n)


INSUFFICIENT = {
    "no claims at all": [],
    "a single verified evidence item": [rat(LIT1), NOVELTY],
    "the same reference twice on one claim": [rat(LIT1, ev(S.LITERATURE, "W1", n=7)), NOVELTY],
    "the same reference on two claims": [rat(LIT1, n=0), rat(ev(S.LITERATURE, "W1", n=8), n=1), NOVELTY],
    "second item unverified": [rat(LIT1, ev_u(S.VAULT, "d1.c1", None, 1)), NOVELTY],
    "second item failed": [rat(LIT1, ev_u(S.VAULT, "d1.c1", False, 1)), NOVELTY],
    "evidence on a score claim does not count": [
        rat(LIT1),
        score_claim(COUNT1, ev(S.LITERATURE, "W2", n=6)),
    ],
    "two items but only one on a verified claim": [rat(LIT1), rat(LIT2, status=CS.UNVERIFIED, n=1), NOVELTY],
}
for _status in (CS.PENDING, CS.UNVERIFIED, CS.CONTRADICTED, CS.REJECTED_BY_CRITIC):
    INSUFFICIENT[f"second item on a {_status.value} claim"] = [
        rat(LIT1, n=0),
        rat(LIT2, status=_status, n=1),
        NOVELTY,
    ]
NO_LITERATURE = {
    "two profile works": [rat(PROF1, PROF2), NOVELTY],
    "profile + vault": [rat(PROF1, VAULT1), NOVELTY],
    "vault + count": [rat(VAULT1, COUNT1), NOVELTY],
    "literature only on an unverified claim": [
        rat(PROF1, VAULT1, n=0),
        rat(LIT1, status=CS.UNVERIFIED, n=1),
        NOVELTY,
    ],
    "literature only on a score claim": [rat(PROF1, VAULT1, n=0), score_claim(COUNT1, LIT1)],
    "literature evidence that failed": [rat(PROF1, VAULT1, ev_u(S.LITERATURE, "W1", False, 8)), NOVELTY],
}
NO_NOVELTY = {
    "no score claim": [rat(LIT1, VAULT1)],
    "score claim not verified": [rat(LIT1, VAULT1), score_claim(status=CS.UNVERIFIED)],
    "score claim pending": [rat(LIT1, VAULT1), score_claim(status=CS.PENDING)],
    "score claim rejected": [rat(LIT1, VAULT1), score_claim(status=CS.REJECTED_BY_CRITIC)],
    "count evidence unverified": [rat(LIT1, VAULT1), score_claim(ev_u(S.COUNT, "n1", None, 0))],
    "count evidence failed": [rat(LIT1, VAULT1), score_claim(ev_u(S.COUNT, "n1", False, 0))],
    "score claim without count evidence": [rat(LIT1, VAULT1), score_claim(ev(S.LITERATURE, "W3", n=0))],
    "count evidence on a rationale claim is not a score claim": [rat(LIT1, COUNT1)],
}


@pytest.mark.parametrize("name", list(INSUFFICIENT))
def test_not_shown_without_two_distinct_verified_evidence_items(name: str) -> None:
    ok, reason = opp(copy.deepcopy(INSUFFICIENT[name])).show_decision()
    assert ok is False and reason


@pytest.mark.parametrize("name", list(NO_LITERATURE))
def test_not_shown_without_a_literature_source(name: str) -> None:
    ok, reason = opp(copy.deepcopy(NO_LITERATURE[name])).show_decision()
    assert ok is False and reason


@pytest.mark.parametrize("name", list(NO_NOVELTY))
def test_not_shown_without_a_measured_novelty_count(name: str) -> None:
    ok, reason = opp(copy.deepcopy(NO_NOVELTY[name])).show_decision()
    assert ok is False and reason == "no_novelty_measure"


def test_not_shown_without_scores_even_with_everything_else() -> None:
    ok, reason = opp(showable_claims(), with_scores=False).show_decision()
    assert ok is False and reason == "no_novelty_measure"


def test_reasons_are_non_empty_stable_and_tell_the_causes_apart() -> None:
    def reason(claims: list[Claim]) -> str:
        o = opp(copy.deepcopy(claims))
        first = o.show_decision()
        assert first == o.show_decision(), "the decision must be repeatable"
        assert first[0] is False and isinstance(first[1], str) and first[1]
        return first[1]

    not_enough = reason(INSUFFICIENT["a single verified evidence item"])
    no_lit = reason(NO_LITERATURE["two profile works"])
    no_nov = reason(NO_NOVELTY["no score claim"])
    assert len({not_enough, no_lit, no_nov}) == 3
    assert reason(INSUFFICIENT["no claims at all"]) != ""


def test_show_decision_does_not_change_the_aggregate() -> None:
    o = opp(showable_claims())
    before = copy.deepcopy(o)
    o.show_decision()
    assert o == before


# ---- the property: an independent oracle agrees ------------------------------------------------------------------
def oracle(o: Opportunity) -> bool:
    """The rule of DECISIONS 59, written without looking at the implementation."""
    pairs: set[tuple[str, str]] = set()
    has_novelty = False
    for c in o.claims:
        if c.status != "verified":
            continue
        if c.section in ("rationale", "objection"):
            pairs.update((str(e.source_type), e.ref) for e in c.evidence if e.verified is True)
        elif c.section == "score" and any(
            e.verified is True and e.source_type == "count" for e in c.evidence
        ):
            has_novelty = True
    literature = any(t == "literature" for t, _ in pairs)
    return len(pairs) >= 2 and literature and has_novelty and o.scores is not None


@settings(max_examples=500, deadline=None)
@given(random_opps())
@example(opp(showable_claims()))
def test_show_decision_agrees_with_an_independent_oracle(o: Opportunity) -> None:
    ok, reason = o.show_decision()
    assert ok == oracle(o)
    if not ok:
        assert reason


@settings(max_examples=300, deadline=None)
@given(random_opps(bias_verified=False))
def test_a_shown_opportunity_always_has_two_distinct_verified_items_one_of_them_literature(
    o: Opportunity,
) -> None:
    if not o.show_decision()[0]:
        return
    items = {
        (e.source_type, e.ref) for c in o.visible_claims() if c.section is not SEC.SCORE for e in c.evidence
    }
    assert len(items) >= 2 and any(t is S.LITERATURE for t, _ in items)
    assert o.scores is not None
    assert any(
        c.section is SEC.SCORE and any(e.source_type is S.COUNT for e in c.evidence)
        for c in o.visible_claims()
    )


# ================================================================================================================
# set_user_status
# ================================================================================================================
USER = [OppStatus.SHOWN, OppStatus.SAVED, OppStatus.DISMISSED]
NON_USER = [s for s in OppStatus if s not in USER]


def test_user_statuses_are_shown_saved_dismissed() -> None:
    assert USER_STATUSES == frozenset(USER)


@pytest.mark.parametrize(("old", "new"), [(a, b) for a, b in product(USER, USER) if a != b])
def test_the_owner_can_move_between_user_statuses_in_any_order(old: OppStatus, new: OppStatus) -> None:
    o = opp(showable_claims(), status=old)
    o.set_user_status(new)
    assert o.status is new


def test_a_walk_through_all_user_statuses_and_back() -> None:
    o = opp(showable_claims(), status=OppStatus.SHOWN)
    for new in (
        OppStatus.DISMISSED,
        OppStatus.SAVED,
        OppStatus.SHOWN,
        OppStatus.SAVED,
        OppStatus.DISMISSED,
        OppStatus.SHOWN,
    ):
        o.set_user_status(new)
        assert o.status is new


@pytest.mark.parametrize("status", USER)
def test_setting_the_same_user_status_again_is_idempotent(status: OppStatus) -> None:
    o = opp(showable_claims(), status=status)
    o.set_user_status(status)
    assert o.status is status


@pytest.mark.parametrize(("old", "new"), list(product(NON_USER, USER)))
def test_a_non_visible_opportunity_cannot_be_given_a_user_status(old: OppStatus, new: OppStatus) -> None:
    o = opp(showable_claims(), status=old)
    before = copy.deepcopy(o)
    with pytest.raises(InvalidStateTransition):
        o.set_user_status(new)
    assert o == before


@pytest.mark.parametrize(("old", "new"), list(product(USER, NON_USER)))
def test_the_owner_cannot_set_a_system_status(old: OppStatus, new: OppStatus) -> None:
    o = opp(showable_claims(), status=old)
    before = copy.deepcopy(o)
    with pytest.raises(InvalidStateTransition):
        o.set_user_status(new)
    assert o == before


@pytest.mark.parametrize(("old", "new"), list(product(NON_USER, NON_USER)))
def test_system_statuses_are_not_reachable_through_the_user_api(old: OppStatus, new: OppStatus) -> None:
    with pytest.raises(InvalidStateTransition):
        opp(status=old).set_user_status(new)
