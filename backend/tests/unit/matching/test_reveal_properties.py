"""Hypothesis properties of the double opt-in reveal rule: invariants I1-I6 (plan M5 task 2; ARCHITECTURE §4.3;
docs/design/M5-matching.md §1 "Invariants", §8 "Unit, Hypothesis").

Assumed API (see test_proposal.py and _builders.py for the full list):
* MatchProposal.decide / expire / close_for_consent / status_for as in the design note.
* scout.matching.domain.reveal: reveal_allowed(state) -> bool;
  build_revealed(p, viewer, contact, ideas) -> RevealedMatchView (frozen pydantic, ``kind == "revealed"``),
  raises InvariantViolation unless the state is MUTUAL; ``Contact(display_name, institution, email, orcid,
  openalex_ids, region, career_band)`` lives in the same module.

Findings that the implementation must honour for I3 to hold (the design table is silent or contradicts it):
1. declined-by-OTHER and now >= expires_at: every action by the viewer must raise 409 match_expired (like a
   pending match would), not be recorded silently.
2. declined-by-OTHER and the viewer's own decision is DECLINE: accept/withdraw must raise 409 match_closed (as
   in "declined by own"); only the viewer's own decision decides, as if the other side had never decided.
3. declined-by-OTHER and the viewer's own decision is not ACCEPT: withdraw must raise 409 nothing_to_withdraw.
In short: the viewer's responses must equal those of a match in which the other side did nothing.
4. S' (the cut history) replaces B's first decline or consent close by B withdrawing its own accept: "B did
   nothing" is the reference world (a B accept followed by a decline would otherwise make A's accept reveal).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule
from tests.unit.matching._builders import A_ID, B_ID, EXPIRES_AT, NOW, VIEWER, make_proposal

from scout.matching.domain.enums import (
    Action,
    CareerBand,
    Decision,
    MatchState,
    Region,
    Side,
    ViewerStatus,
)
from scout.matching.domain.reveal import Contact, build_revealed, reveal_allowed
from scout.shared import InvariantViolation, ScoutError

PROFILE = settings(
    max_examples=100,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
    database=None,
)
PENDING = {MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED}
TERMINAL = {MatchState.MUTUAL, MatchState.DECLINED, MatchState.EXPIRED}
CONTACT = Contact(
    display_name="Test Person",
    institution=None,
    email="x@example.edu",
    orcid=None,
    openalex_ids=[],
    region=Region.EUROPE,
    career_band=CareerBand.MID,
)

sides = st.sampled_from([Side.A, Side.B])
actions = st.sampled_from(list(Action))


# ------------------------------------------------------------------ step language

act_step = st.tuples(st.just("act"), sides, actions, st.booleans())
adv_step = st.tuples(st.just("adv"), st.integers(0, 30))
expire_step = st.tuples(st.just("expire"))
close_step = st.tuples(st.just("close"), sides)
steps_st = st.lists(st.one_of(act_step, act_step, adv_step, expire_step, close_step), max_size=14)
# I3 differential: A never withdraws consent (A would not be observing); B may.
i3_steps = st.lists(
    st.one_of(
        act_step,
        act_step,
        adv_step,
        expire_step,
        st.tuples(st.just("close"), st.just(Side.B)),
    ),
    max_size=14,
)
# I4: B only accepts and withdraws.
b_passive = st.tuples(
    st.just("act"), st.just(Side.B), st.sampled_from([Action.ACCEPT, Action.WITHDRAW]), st.just(False)
)
i4_steps = st.lists(
    st.one_of(act_step.filter(lambda s: s[1] is Side.A), b_passive, adv_step, expire_step), max_size=14
)


def _respond(p, viewer, action, now, never=False) -> tuple:
    try:
        events = p.decide(viewer, action, now, never_similar=never)
    except ScoutError as exc:
        return ("err", exc.code, exc.http_status)
    return ("ok", tuple(type(e).__name__ for e in events))


def run(steps, mode: str) -> list[tuple]:
    """Replay ``steps`` and return what A observes after each step.

    mode "full": everything applies. "cut" (S'): B's first decline/consent close is replaced by B withdrawing
    its accept and B does nothing afterwards. "skip_b": every B step is ignored (log stays aligned).
    """
    p = make_proposal()
    now = NOW
    cut = False
    log: list[tuple] = []
    for step in steps:
        response: tuple = ("-",)
        kind = step[0]
        if kind == "adv":
            now += timedelta(days=step[1])
        elif kind == "expire":
            p.expire(now)
        elif kind == "close":
            side = step[1]
            if side is Side.B and mode == "skip_b":
                pass
            elif side is Side.B and mode == "cut" and not cut:
                cut = True
                _respond(p, B_ID, Action.WITHDRAW, now)
            elif side is Side.B and mode == "cut":
                pass
            else:
                p.close_for_consent(side, now)
        else:
            _, side, action, never = step
            if side is Side.A:
                response = _respond(p, A_ID, action, now, never)
            elif mode == "skip_b" or (mode == "cut" and cut):
                pass
            elif mode == "cut" and action is Action.DECLINE:
                cut = True
                _respond(p, B_ID, Action.WITHDRAW, now)
            else:
                _respond(p, B_ID, action, now, never)
        log.append((response, p.status_for(A_ID, now)))
    return log


def _revealed(entry: tuple) -> bool:
    response, status = entry
    return status is ViewerStatus.MUTUAL or (response[0] == "ok" and "MatchBecameMutual" in response[1])


# ------------------------------------------------------------------ I3 and I4


@PROFILE
@given(steps=i3_steps)
def test_i3_decline_secrecy_cut_history_is_indistinguishable(steps) -> None:
    """Everything A observes (status, responses, error codes) is identical with and without B's decline."""
    assert run(steps, "full") == run(steps, "cut")


@PROFILE
@given(steps=i4_steps)
def test_i4_a_never_observes_b_decision_before_the_reveal(steps) -> None:
    with_b, without_b = run(steps, "full"), run(steps, "skip_b")
    first = [next((i for i, e in enumerate(log) if _revealed(e)), len(log)) for log in (with_b, without_b)]
    k = min(first)
    assert with_b[:k] == without_b[:k]


# ------------------------------------------------------------------ stateful: I1, I2, I5, I6


class RevealMachine(RuleBasedStateMachine):
    """Random walks over every operation; invariants are checked after every step."""

    @initialize()
    def start(self) -> None:
        self.p = make_proposal()
        self.now = NOW
        self.prev_state = self.p.state

    @rule(side=sides, action=actions, never=st.booleans())
    def decide(self, side, action, never) -> None:
        before = self.p.state
        viewer_side_accepted = {Side.A: self.p.a_decision, Side.B: self.p.b_decision}[side] is Decision.ACCEPT
        outcome = _respond(self.p, VIEWER[side], action, self.now, never)
        if action is Action.WITHDRAW and outcome[0] == "ok" and before in PENDING and self.p.state in PENDING:
            # I6: withdraw works only from own_accepted and leads to proposed.
            assert viewer_side_accepted and self.p.state is MatchState.PROPOSED

    @rule(days=st.integers(0, 30))
    def advance(self, days) -> None:
        self.now += timedelta(days=days)

    @rule()
    def expire(self) -> None:
        self.p.expire(self.now)

    @rule(side=sides)
    def close(self, side) -> None:
        self.p.close_for_consent(side, self.now)

    @invariant()
    def i1_mutual_iff_both_accepted_iff_revealed(self) -> None:
        p = self.p
        both = p.a_decision is Decision.ACCEPT and p.b_decision is Decision.ACCEPT
        assert (p.state is MatchState.MUTUAL) == both == (p.revealed_at is not None)

    @invariant()
    def i2_reveal_only_when_mutual(self) -> None:
        p = self.p
        assert reveal_allowed(p.state) == (p.state is MatchState.MUTUAL)
        if p.state is MatchState.MUTUAL:
            assert build_revealed(p, A_ID, CONTACT, []).kind == "revealed"
        else:
            with pytest.raises(InvariantViolation):
                build_revealed(p, A_ID, CONTACT, [])

    @invariant()
    def i5_terminal_states_are_never_left(self) -> None:
        if self.prev_state in TERMINAL:
            assert self.p.state is self.prev_state
        self.prev_state = self.p.state

    @invariant()
    def state_is_consistent_with_decisions(self) -> None:
        p = self.p
        if p.state is MatchState.PROPOSED:
            assert p.a_decision is None and p.b_decision is None
        if p.state is MatchState.A_ACCEPTED:
            assert p.a_decision is Decision.ACCEPT and p.b_decision is None
        if p.state is MatchState.B_ACCEPTED:
            assert p.b_decision is Decision.ACCEPT and p.a_decision is None
        if p.state is MatchState.DECLINED:
            assert p.declined_by is not None and p.close_reason is not None and p.closed_at is not None
        if p.state is not MatchState.DECLINED:
            assert p.declined_by is None

    @invariant()
    def status_depends_only_on_own_decision_unless_mutual(self) -> None:
        p = self.p
        for side, viewer in VIEWER.items():
            status = p.status_for(viewer, self.now)
            own = p.a_decision if side is Side.A else p.b_decision
            if p.state is MatchState.MUTUAL:
                assert status is ViewerStatus.MUTUAL
            elif p.declined_by is side or own is Decision.DECLINE:
                # own decline wins over the clock, silent or not (needed for I3: the other side did nothing)
                assert status is ViewerStatus.DECLINED_BY_YOU
            elif p.state is MatchState.EXPIRED or self.now >= EXPIRES_AT:
                assert status is ViewerStatus.EXPIRED
            else:
                expected = {
                    Decision.ACCEPT: ViewerStatus.WAITING_ON_THEM,
                    Decision.DECLINE: ViewerStatus.DECLINED_BY_YOU,
                    None: ViewerStatus.AWAITING_YOU,
                }[own]
                assert status is expected


RevealMachine.TestCase.settings = settings(PROFILE, stateful_step_count=30, max_examples=100)
TestRevealMachine = RevealMachine.TestCase


# ------------------------------------------------------------------ I2 / I5 on explicit sequences


@PROFILE
@given(steps=steps_st)
def test_build_revealed_succeeds_iff_mutual_after_any_sequence(steps) -> None:
    p = make_proposal()
    now = NOW
    for step in steps:
        if step[0] == "adv":
            now += timedelta(days=step[1])
        elif step[0] == "expire":
            p.expire(now)
        elif step[0] == "close":
            p.close_for_consent(step[1], now)
        else:
            _respond(p, VIEWER[step[1]], step[2], now, step[3])
    if p.state is MatchState.MUTUAL:
        assert build_revealed(p, B_ID, CONTACT, []).kind == "revealed"
    else:
        with pytest.raises(InvariantViolation):
            build_revealed(p, B_ID, CONTACT, [])
