"""M6 slice A, matching domain: ``MatchProposal.detach``, ``StoredRationale.without``, ``CloseReason.ACCOUNT_DELETED``
(plan M6 task 2; ARCHITECTURE section 4.3; docs/design/M6-deletion.md section 1.1 "Matching detach", section 5
"Hypothesis", decision 89).

The security property: when a user deletes their account, the counterpart learns nothing beyond "the proposal
became expired". A detached proposal must therefore look to the counterpart exactly like a consent close
(``close_for_consent``), which the M5 properties (test_reveal_properties.py) already prove indistinguishable from
a decline and from silence.

Assumed API (design note):
* ``MatchProposal.detach(side: Side, now: datetime) -> None``: raises ``InvariantViolation`` ("detach_mutual")
  when the state is MUTUAL; a pending proposal goes through ``_decline(side, now, False, ACCOUNT_DELETED)``
  (state DECLINED, ``declined_by = side``); then ``<side>_id = None``, ``<side>_decision`` and
  ``<side>_decided_at`` cleared, ``never_similar_by`` cleared when it equals ``side``, and
  ``rationale = rationale.without(side)``. Terminal declined/expired proposals only get the clean-up. No event.
* ``StoredRationale.without(side) -> StoredRationale``: the rationale is stored from side A's point of view
  (``you_bring`` is A's, ``they_bring`` is B's); the deleted side's list and ``why`` become empty and every
  ``source_refs`` (which point to the deleted user's works) is cleared. Returns a new frozen value.
* ``MatchProposal.a_id`` / ``b_id`` are ``AccountId | None``. ``score_basis`` and both public facets are kept
  (the survivor's card shows them; changing them would leak).
"""

from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule
from tests.unit.matching._builders import A_ID, B_ID, EXPIRES_AT, NOW, VIEWER, make_proposal

from scout.matching.domain.enums import Action, CloseReason, Decision, MatchState, Side, ViewerStatus
from scout.matching.domain.proposal import BringItem, MatchProposal, StoredRationale
from scout.shared import InvariantViolation, ScoutError

PROFILE = settings(
    max_examples=150,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    database=None,
)
SIDES = [Side.A, Side.B]
sides = st.sampled_from(SIDES)
actions = st.sampled_from(list(Action))
LATER = NOW + timedelta(days=2)


def _id_of(p: MatchProposal, side: Side):
    return p.a_id if side is Side.A else p.b_id


def _decided_at(p: MatchProposal, side: Side):
    return p.a_decided_at if side is Side.A else p.b_decided_at


def _rich_rationale() -> StoredRationale:
    return StoredRationale(
        source="llm",
        you_bring=[BringItem(text="lignin chemistry", source_refs=["W1", "W2"])],
        they_bring=[BringItem(text="foam rheology", source_refs=["W9"])],
        why="Complementary methods.",
        prompt_version="MatchExplainer@1.0.0",
    )


def _refs(r: StoredRationale) -> list[str]:
    return [ref for item in (*r.you_bring, *r.they_bring) for ref in item.source_refs]


def _in_state(name: str) -> MatchProposal:
    """A proposal in each reachable state (A is the viewer of the setup actions)."""
    p = make_proposal()
    if name == "a_accepted":
        p.decide(A_ID, Action.ACCEPT, NOW)
    elif name == "b_accepted":
        p.decide(B_ID, Action.ACCEPT, NOW)
    elif name == "mutual":
        p.decide(A_ID, Action.ACCEPT, NOW)
        p.decide(B_ID, Action.ACCEPT, NOW)
    elif name == "declined_by_a":
        p.decide(A_ID, Action.DECLINE, NOW, never_similar=True)
    elif name == "declined_by_b":
        p.decide(B_ID, Action.DECLINE, NOW, never_similar=True)
    elif name == "expired":
        p.expire(EXPIRES_AT)
    return p


# ------------------------------------------------------------------ enum and rationale


def test_close_reason_account_deleted_value() -> None:
    assert CloseReason.ACCOUNT_DELETED.value == "account_deleted"
    assert {r.value for r in CloseReason} >= {"declined", "consent_withdrawn", "account_deleted"}


def test_rationale_without_a_clears_a_side_why_and_every_ref() -> None:
    original = _rich_rationale()
    out = original.without(Side.A)
    assert out.you_bring == [] and out.why == ""
    assert _refs(out) == []
    assert out.source == "llm" and out.prompt_version == "MatchExplainer@1.0.0"
    assert original == _rich_rationale()  # frozen value: the input is untouched


def test_rationale_without_b_clears_b_side_why_and_every_ref() -> None:
    out = _rich_rationale().without(Side.B)
    assert out.they_bring == [] and out.why == ""
    assert _refs(out) == []


@pytest.mark.parametrize("side", SIDES)
def test_rationale_without_is_idempotent_and_valid_on_empty(side: Side) -> None:
    once = _rich_rationale().without(side)
    assert once.without(side) == once
    empty = StoredRationale(source="template")
    assert empty.without(side) == empty


# ------------------------------------------------------------------ detach on each state


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("state", ["proposed", "a_accepted", "b_accepted"])
def test_detach_pending_closes_like_a_consent_close_and_forgets_the_side(side: Side, state: str) -> None:
    p = _in_state(state)
    p.rationale = _rich_rationale()
    keep = (p.score, p.score_basis, p.a_public, p.b_public, p.field, p.expires_at, p.id)
    p.pull_events()

    p.detach(side, LATER)

    assert p.state is MatchState.DECLINED
    assert p.declined_by is side and p.close_reason is CloseReason.ACCOUNT_DELETED
    assert p.closed_at == LATER
    assert _id_of(p, side) is None and _id_of(p, side.other) == VIEWER[side.other]
    assert p.decision_of(side) is None and _decided_at(p, side) is None
    assert p.rationale == _rich_rationale().without(side)
    assert (p.score, p.score_basis, p.a_public, p.b_public, p.field, p.expires_at, p.id) == keep
    assert p.pull_events() == []  # there is deliberately no deletion event


@pytest.mark.parametrize("side", SIDES)
def test_detach_keeps_the_survivor_decision(side: Side) -> None:
    p = make_proposal()
    p.decide(VIEWER[side.other], Action.ACCEPT, NOW)
    p.detach(side, LATER)
    assert p.decision_of(side.other) is Decision.ACCEPT and p.decision_of(side) is None


@pytest.mark.parametrize("side", SIDES)
def test_detach_clears_never_similar_only_for_the_detached_side(side: Side) -> None:
    mine = _in_state("declined_by_a" if side is Side.A else "declined_by_b")
    assert mine.never_similar_by is side
    mine.detach(side, LATER)
    assert mine.never_similar_by is None

    theirs = _in_state("declined_by_b" if side is Side.A else "declined_by_a")
    assert theirs.never_similar_by is side.other
    theirs.detach(side, LATER)
    assert theirs.never_similar_by is side.other  # the survivor's block intent is not the leaver's to erase


@pytest.mark.parametrize("side", SIDES)
@pytest.mark.parametrize("state", ["declined_by_a", "declined_by_b", "expired"])
def test_detach_terminal_proposal_keeps_state_and_reason(side: Side, state: str) -> None:
    p = _in_state(state)
    before = (p.state, p.declined_by, p.close_reason, p.closed_at)
    p.detach(side, LATER)
    assert (p.state, p.declined_by, p.close_reason, p.closed_at) == before
    assert _id_of(p, side) is None and _id_of(p, side.other) is not None


@pytest.mark.parametrize("side", SIDES)
def test_detach_mutual_raises_and_changes_nothing(side: Side) -> None:
    p = _in_state("mutual")
    snapshot = copy.deepcopy(p)
    with pytest.raises(InvariantViolation) as info:
        p.detach(side, LATER)
    assert info.value.detail == "detach_mutual"
    assert p == snapshot


@pytest.mark.parametrize("side", SIDES)
def test_detach_twice_is_harmless(side: Side) -> None:
    p = make_proposal()
    p.detach(side, LATER)
    once = copy.deepcopy(p)
    p.detach(side, LATER + timedelta(days=1))
    assert p == once


@pytest.mark.parametrize("side", SIDES)
def test_the_detached_account_can_no_longer_act_or_be_found(side: Side) -> None:
    p = make_proposal()
    p.detach(side, LATER)
    with pytest.raises(ScoutError) as info:
        p.side_of(VIEWER[side])
    assert info.value.code == "match_not_found"
    assert p.side_of(VIEWER[side.other]) is side.other


# ------------------------------------------------------------------ what the survivor sees (examples)


@pytest.mark.parametrize("side", SIDES)
def test_survivor_who_accepted_sees_waiting_then_expired(side: Side) -> None:
    survivor = VIEWER[side.other]
    p = make_proposal()
    p.decide(survivor, Action.ACCEPT, NOW)
    p.detach(side, LATER)
    assert p.status_for(survivor, LATER) is ViewerStatus.WAITING_ON_THEM
    assert p.status_for(survivor, EXPIRES_AT) is ViewerStatus.EXPIRED


@pytest.mark.parametrize("side", SIDES)
def test_survivor_who_did_nothing_sees_awaiting_you_then_expired(side: Side) -> None:
    survivor = VIEWER[side.other]
    p = make_proposal()
    p.detach(side, LATER)
    assert p.status_for(survivor, LATER) is ViewerStatus.AWAITING_YOU
    assert p.status_for(survivor, EXPIRES_AT) is ViewerStatus.EXPIRED


@pytest.mark.parametrize("side", SIDES)
def test_survivor_accept_after_detach_never_reveals(side: Side) -> None:
    survivor = VIEWER[side.other]
    p = make_proposal()
    p.detach(side, LATER)
    assert p.decide(survivor, Action.ACCEPT, LATER) == []
    assert p.state is not MatchState.MUTUAL and p.revealed_at is None


# ------------------------------------------------------------------ differential: detach == consent close for the survivor

pre_act = st.tuples(st.just("act"), sides, actions, st.booleans())
pre_adv = st.tuples(st.just("adv"), st.integers(0, 30))
pre_expire = st.tuples(st.just("expire"))
pre_close = st.tuples(st.just("close"), sides)
prefix_st = st.lists(st.one_of(pre_act, pre_act, pre_adv, pre_expire, pre_close), max_size=10)
tail_act = st.tuples(st.just("act"), actions, st.booleans())
tail_st = st.lists(st.one_of(tail_act, tail_act, pre_adv, pre_expire), max_size=10)


def _respond(p: MatchProposal, viewer, action: Action, now, never: bool = False) -> tuple:
    try:
        events = p.decide(viewer, action, now, never_similar=never)
    except ScoutError as exc:
        return ("err", exc.code, exc.http_status)
    return ("ok", tuple(type(e).__name__ for e in events))


def _replay_prefix(prefix):
    p, now = make_proposal(), NOW
    for step in prefix:
        if step[0] == "adv":
            now += timedelta(days=step[1])
        elif step[0] == "expire":
            p.expire(now)
        elif step[0] == "close":
            p.close_for_consent(step[1], now)
        else:
            _respond(p, VIEWER[step[1]], step[2], now, step[3])
    return p, now


def _survivor_log(p: MatchProposal, survivor, now, tail) -> list[tuple]:
    """Everything the survivor observes: status and the answer of every action after the other side left."""
    log: list[tuple] = [("-", p.status_for(survivor, now))]
    for step in tail:
        response: tuple = ("-",)
        if step[0] == "adv":
            now += timedelta(days=step[1])
        elif step[0] == "expire":
            p.expire(now)
        else:
            response = _respond(p, survivor, step[1], now, step[2])
        log.append((response, p.status_for(survivor, now)))
    return log


@PROFILE
@given(prefix=prefix_st, leaver=sides, gap=st.integers(0, 20), tail=tail_st)
def test_detach_is_indistinguishable_from_a_consent_close_for_the_survivor(prefix, leaver, gap, tail) -> None:
    p, now = _replay_prefix(prefix)
    assume(p.state is not MatchState.MUTUAL)  # mutual proposals are deleted, never detached
    now += timedelta(days=gap)
    via_detach, via_close = copy.deepcopy(p), copy.deepcopy(p)
    via_detach.detach(leaver, now)
    via_close.close_for_consent(leaver, now)
    survivor = VIEWER[leaver.other]
    assert _survivor_log(via_detach, survivor, now, tail) == _survivor_log(via_close, survivor, now, tail)


@PROFILE
@given(prefix=prefix_st, leaver=sides, gap=st.integers(0, 20))
def test_detach_after_any_history_leaves_a_clean_survivor_only_proposal(prefix, leaver, gap) -> None:
    p, now = _replay_prefix(prefix)
    assume(p.state is not MatchState.MUTUAL)
    now += timedelta(days=gap)
    p.rationale = _rich_rationale()
    p.detach(leaver, now)
    assert _id_of(p, leaver) is None and _id_of(p, leaver.other) == VIEWER[leaver.other]
    assert p.decision_of(leaver) is None and _decided_at(p, leaver) is None
    assert p.never_similar_by is not leaver
    assert p.state in (MatchState.DECLINED, MatchState.EXPIRED)
    assert p.revealed_at is None
    assert p.rationale == _rich_rationale().without(leaver) and _refs(p.rationale) == []


@PROFILE
@given(prefix=prefix_st, leaver=sides)
def test_detach_raises_exactly_when_the_proposal_is_mutual(prefix, leaver) -> None:
    p, _ = _replay_prefix(prefix)
    if p.state is MatchState.MUTUAL:
        with pytest.raises(InvariantViolation):
            p.detach(leaver, LATER)
    else:
        p.detach(leaver, LATER)  # must not raise


# ------------------------------------------------------------------ stateful: detach joins the M5 random walk


class DetachMachine(RuleBasedStateMachine):
    """Random walks over every operation, with at most one account deletion in the middle."""

    @initialize()
    def start(self) -> None:
        self.p = make_proposal()
        self.now = NOW
        self.left: Side | None = None

    def _may_act(self, side: Side) -> bool:
        return self.left is not side

    @rule(side=sides, action=actions, never=st.booleans())
    def decide(self, side, action, never) -> None:
        if self._may_act(side):
            _respond(self.p, VIEWER[side], action, self.now, never)

    @rule(days=st.integers(0, 30))
    def advance(self, days) -> None:
        self.now += timedelta(days=days)

    @rule()
    def expire(self) -> None:
        self.p.expire(self.now)

    @rule(side=sides)
    def close(self, side) -> None:
        if self._may_act(side):
            self.p.close_for_consent(side, self.now)

    @precondition(lambda self: self.left is None)
    @rule(side=sides)
    def detach(self, side) -> None:
        if self.p.state is MatchState.MUTUAL:
            with pytest.raises(InvariantViolation):
                self.p.detach(side, self.now)
            return
        self.p.rationale = _rich_rationale()
        self.p.detach(side, self.now)
        self.left = side

    @invariant()
    def mutual_proposals_are_never_detached(self) -> None:
        if self.left is not None:
            assert self.p.state is not MatchState.MUTUAL and self.p.revealed_at is None

    @invariant()
    def leaver_is_forgotten_and_survivor_is_intact(self) -> None:
        if self.left is None:
            return
        p, gone = self.p, self.left
        assert _id_of(p, gone) is None and _id_of(p, gone.other) == VIEWER[gone.other]
        assert p.decision_of(gone) is None and _decided_at(p, gone) is None
        assert p.never_similar_by is not gone
        assert _refs(p.rationale) == [] and p.rationale.why == ""

    @invariant()
    def survivor_status_is_never_a_reveal_or_a_decline_by_the_other(self) -> None:
        if self.left is None:
            return
        status = self.p.status_for(VIEWER[self.left.other], self.now)
        assert status in (
            ViewerStatus.AWAITING_YOU,
            ViewerStatus.WAITING_ON_THEM,
            ViewerStatus.DECLINED_BY_YOU,
            ViewerStatus.EXPIRED,
        )


DetachMachine.TestCase.settings = settings(PROFILE, stateful_step_count=30, max_examples=100)
TestDetachMachine = DetachMachine.TestCase
