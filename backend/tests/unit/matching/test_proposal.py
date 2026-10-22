"""MatchProposal state machine, one example per row of the design table (plan M5 tasks 1-2; ARCHITECTURE §4.3,
docs/design/M5-matching.md §1, DECISIONS 77-85).

Assumed signatures (scout.matching.domain.*):
* enums: MatchState{PROPOSED,A_ACCEPTED,B_ACCEPTED,MUTUAL,DECLINED,EXPIRED}, Side{A,B}, Decision{ACCEPT,DECLINE},
  Action{ACCEPT,DECLINE,WITHDRAW}, ViewerStatus{AWAITING_YOU,WAITING_ON_THEM,MUTUAL,DECLINED_BY_YOU,EXPIRED},
  CloseReason{DECLINED,CONSENT_WITHDRAWN}, IdeasStatus{NONE,...}.
* MatchProposal.propose(...) (see _builders.py); fields id, a_id, b_id, state, a_decision, b_decision,
  a_decided_at, b_decided_at, declined_by, close_reason, never_similar_by, created_at, expires_at, revealed_at,
  closed_at, ideas_status.
* p.decide(viewer, action, now, never_similar=False) -> list[DomainEvent]; errors are ScoutError subclasses with
  ``code`` and ``http_status`` ("match_expired"/"match_closed"/"nothing_to_withdraw" 409; non-participant 404;
  never_similar without decline 422).
* p.expire(now) -> bool; p.close_for_consent(side, now) -> None; p.status_for(viewer, now) -> ViewerStatus.
* The decide() failure for an expired match leaves ``p.state is EXPIRED`` (the table says expire(), then 409).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.unit.matching._builders import (
    A_ID,
    B_ID,
    EXPIRES_AT,
    NOW,
    STRANGER,
    VIEWER,
    apply,
    error_of,
    make_proposal,
)

from scout.matching.domain.enums import (
    Action,
    CloseReason,
    Decision,
    IdeasStatus,
    MatchState,
    Side,
    ViewerStatus,
)
from scout.shared import InvariantViolation

LATER = NOW + timedelta(days=1)


def names(events) -> list[str]:
    return [type(e).__name__ for e in events]


def reach(state: MatchState):
    """Build a proposal in ``state`` (A is the viewer of interest; other = B)."""
    p = make_proposal()
    if state is MatchState.A_ACCEPTED:
        apply(p, Side.A, Action.ACCEPT)
    elif state is MatchState.B_ACCEPTED:
        apply(p, Side.B, Action.ACCEPT)
    elif state is MatchState.MUTUAL:
        apply(p, Side.B, Action.ACCEPT)
        apply(p, Side.A, Action.ACCEPT, LATER)
    elif state is MatchState.DECLINED:
        apply(p, Side.B, Action.DECLINE)
    elif state is MatchState.EXPIRED:
        assert p.expire(EXPIRES_AT)
    assert p.state is state
    return p


# ---------------------------------------------------------------- propose


def test_propose_sets_initial_state_and_expiry() -> None:
    p = make_proposal(score=0.5)
    assert p.state is MatchState.PROPOSED
    assert (p.a_id, p.b_id) == (A_ID, B_ID)
    assert p.a_decision is None and p.b_decision is None
    assert p.declined_by is None and p.close_reason is None and p.never_similar_by is None
    assert p.revealed_at is None and p.closed_at is None
    assert p.ideas_status is IdeasStatus.NONE
    assert p.created_at == NOW and p.expires_at == EXPIRES_AT
    assert names(p.pull_events()) == ["MatchProposed"]


@pytest.mark.parametrize("a,b", [(B_ID, A_ID), (A_ID, A_ID)])
def test_propose_requires_ordered_distinct_ids(a, b) -> None:
    with pytest.raises(InvariantViolation):
        make_proposal(a_id=a, b_id=b)


@pytest.mark.parametrize("score", [-0.001, 1.001, float("nan")])
def test_propose_rejects_score_out_of_range(score) -> None:
    with pytest.raises(InvariantViolation):
        make_proposal(score=score)


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_propose_accepts_score_bounds(score) -> None:
    assert make_proposal(score=score).score == score


# ---------------------------------------------------------------- proposed row


@pytest.mark.parametrize(
    "side,expected,decision_attr",
    [(Side.A, MatchState.A_ACCEPTED, "a_decision"), (Side.B, MatchState.B_ACCEPTED, "b_decision")],
)
def test_proposed_accept(side, expected, decision_attr) -> None:
    p = make_proposal()
    p.pull_events()
    assert apply(p, side, Action.ACCEPT) == []
    assert p.state is expected
    assert getattr(p, decision_attr) is Decision.ACCEPT
    assert getattr(p, decision_attr.replace("decision", "decided_at")) == NOW


@pytest.mark.parametrize("side", [Side.A, Side.B])
def test_proposed_decline(side) -> None:
    p = make_proposal()
    assert apply(p, side, Action.DECLINE) == []
    assert p.state is MatchState.DECLINED
    assert p.declined_by is side
    assert p.close_reason is CloseReason.DECLINED
    assert p.closed_at == NOW
    assert getattr(p, f"{side.value}_decision") is Decision.DECLINE  # type: ignore[union-attr]


def test_proposed_withdraw_is_409_nothing_to_withdraw() -> None:
    p = make_proposal()
    exc = error_of(lambda: apply(p, Side.A, Action.WITHDRAW))
    assert (exc.code, exc.http_status) == ("nothing_to_withdraw", 409)
    assert p.state is MatchState.PROPOSED


# ---------------------------------------------------------------- own_accepted row


def test_own_accepted_accept_is_noop() -> None:
    p = reach(MatchState.A_ACCEPTED)
    assert apply(p, Side.A, Action.ACCEPT, LATER) == []
    assert p.state is MatchState.A_ACCEPTED and p.a_decided_at == NOW


def test_own_accepted_decline() -> None:
    p = reach(MatchState.A_ACCEPTED)
    apply(p, Side.A, Action.DECLINE, LATER)
    assert p.state is MatchState.DECLINED and p.declined_by is Side.A


def test_own_accepted_withdraw_returns_to_proposed() -> None:
    p = reach(MatchState.A_ACCEPTED)
    assert apply(p, Side.A, Action.WITHDRAW, LATER) == []
    assert p.state is MatchState.PROPOSED and p.a_decision is None


# ---------------------------------------------------------------- other_accepted row


def test_other_accepted_accept_makes_mutual_and_emits_event() -> None:
    p = reach(MatchState.B_ACCEPTED)
    events = apply(p, Side.A, Action.ACCEPT, LATER)
    assert names(events) == ["MatchBecameMutual"]
    ev = events[0]
    assert (ev.match_id, ev.a_id, ev.b_id) == (p.id, A_ID, B_ID)
    assert p.state is MatchState.MUTUAL and p.revealed_at == LATER
    assert p.a_decision is Decision.ACCEPT and p.b_decision is Decision.ACCEPT


def test_other_accepted_decline() -> None:
    p = reach(MatchState.B_ACCEPTED)
    apply(p, Side.A, Action.DECLINE, LATER)
    assert p.state is MatchState.DECLINED and p.declined_by is Side.A


def test_other_accepted_withdraw_is_nothing_to_withdraw() -> None:
    p = reach(MatchState.B_ACCEPTED)
    exc = error_of(lambda: apply(p, Side.A, Action.WITHDRAW))
    assert (exc.code, exc.http_status) == ("nothing_to_withdraw", 409)


# ---------------------------------------------------------------- mutual row


@pytest.mark.parametrize("side", [Side.A, Side.B])
def test_mutual_accept_noop_others_closed(side) -> None:
    p = reach(MatchState.MUTUAL)
    snapshot = (p.revealed_at, p.a_decision, p.b_decision)
    assert apply(p, side, Action.ACCEPT, LATER) == []
    for action in (Action.DECLINE, Action.WITHDRAW):
        exc = error_of(lambda a=action: apply(p, side, a, LATER))
        assert (exc.code, exc.http_status) == ("match_closed", 409)
    assert p.state is MatchState.MUTUAL
    assert snapshot == (p.revealed_at, p.a_decision, p.b_decision)


# ---------------------------------------------------------------- declined by own row


def test_declined_by_own_rows() -> None:
    p = make_proposal()
    apply(p, Side.A, Action.DECLINE)
    assert apply(p, Side.A, Action.DECLINE, LATER) == []
    for action in (Action.ACCEPT, Action.WITHDRAW):
        exc = error_of(lambda a=action: apply(p, Side.A, a, LATER))
        assert (exc.code, exc.http_status) == ("match_closed", 409)
    assert p.state is MatchState.DECLINED and p.declined_by is Side.A


# ---------------------------------------------------------------- declined by other row (silent)


def test_declined_by_other_accept_is_recorded_silently() -> None:
    p = reach(MatchState.DECLINED)  # B declined
    before = (p.state, p.declined_by, p.b_decision, p.b_decided_at, p.closed_at, p.close_reason)
    assert apply(p, Side.A, Action.ACCEPT, LATER) == []
    assert p.a_decision is Decision.ACCEPT
    assert (p.state, p.declined_by, p.b_decision, p.b_decided_at, p.closed_at, p.close_reason) == before
    assert p.state is MatchState.DECLINED and p.revealed_at is None
    assert p.status_for(A_ID, LATER) is ViewerStatus.WAITING_ON_THEM


def test_declined_by_other_accept_then_withdraw_toggles_own_decision() -> None:
    p = reach(MatchState.DECLINED)
    apply(p, Side.A, Action.ACCEPT, LATER)
    assert apply(p, Side.A, Action.WITHDRAW, LATER) == []
    assert p.a_decision is None
    assert p.status_for(A_ID, LATER) is ViewerStatus.AWAITING_YOU


def test_declined_by_other_decline_sets_own_decision_and_honours_never_similar() -> None:
    p = reach(MatchState.DECLINED)
    assert apply(p, Side.A, Action.DECLINE, LATER, never_similar=True) == []
    assert p.a_decision is Decision.DECLINE
    assert p.never_similar_by is Side.A
    assert p.declined_by is Side.B
    assert p.status_for(A_ID, LATER) is ViewerStatus.DECLINED_BY_YOU


# ---------------------------------------------------------------- expiry rows


@pytest.mark.parametrize("state", [MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED])
@pytest.mark.parametrize("action", list(Action))
def test_pending_at_or_after_expiry_expires_then_409(state, action) -> None:
    for now in (EXPIRES_AT, EXPIRES_AT + timedelta(days=3)):
        p = reach(state)
        exc = error_of(lambda p=p, now=now: apply(p, Side.A, action, now))
        assert (exc.code, exc.http_status) == ("match_expired", 409)
        assert p.state is MatchState.EXPIRED


def test_just_before_expiry_still_works() -> None:
    p = make_proposal()
    apply(p, Side.A, Action.ACCEPT, EXPIRES_AT - timedelta(seconds=1))
    assert p.state is MatchState.A_ACCEPTED


@pytest.mark.parametrize("action", list(Action))
def test_expired_state_rejects_everything(action) -> None:
    p = reach(MatchState.EXPIRED)
    exc = error_of(lambda: apply(p, Side.B, action, EXPIRES_AT + timedelta(days=1)))
    assert (exc.code, exc.http_status) == ("match_expired", 409)


# ---------------------------------------------------------------- never_similar


@pytest.mark.parametrize("side", [Side.A, Side.B])
def test_never_similar_with_decline_marks_side(side) -> None:
    p = make_proposal()
    apply(p, side, Action.DECLINE, never_similar=True)
    assert p.never_similar_by is side


@pytest.mark.parametrize("action", [Action.ACCEPT, Action.WITHDRAW])
def test_never_similar_without_decline_is_422_and_changes_nothing(action) -> None:
    p = reach(MatchState.A_ACCEPTED)
    exc = error_of(lambda: apply(p, Side.A, action, never_similar=True))
    assert exc.http_status == 422
    assert p.state is MatchState.A_ACCEPTED and p.never_similar_by is None


def test_plain_decline_does_not_set_never_similar() -> None:
    p = make_proposal()
    apply(p, Side.A, Action.DECLINE)
    assert p.never_similar_by is None


# ---------------------------------------------------------------- expire()


@pytest.mark.parametrize("state", [MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED])
def test_expire_moves_pending_only_at_deadline(state) -> None:
    p = reach(state)
    assert p.expire(EXPIRES_AT - timedelta(seconds=1)) is False and p.state is state
    assert p.expire(EXPIRES_AT) is True and p.state is MatchState.EXPIRED
    assert p.expire(EXPIRES_AT + timedelta(days=1)) is False


@pytest.mark.parametrize("state", [MatchState.MUTUAL, MatchState.DECLINED])
def test_expire_ignores_terminal_states(state) -> None:
    p = reach(state)
    assert p.expire(EXPIRES_AT + timedelta(days=30)) is False and p.state is state


# ---------------------------------------------------------------- close_for_consent


@pytest.mark.parametrize("state", [MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED])
@pytest.mark.parametrize("side", [Side.A, Side.B])
def test_close_for_consent_declines_with_reason(state, side) -> None:
    p = reach(state)
    p.close_for_consent(side, LATER)
    assert p.state is MatchState.DECLINED
    assert p.declined_by is side and p.close_reason is CloseReason.CONSENT_WITHDRAWN
    assert p.closed_at == LATER


def test_close_for_consent_leaves_mutual_alone() -> None:
    p = reach(MatchState.MUTUAL)
    p.close_for_consent(Side.A, LATER)
    assert p.state is MatchState.MUTUAL and p.close_reason is None


@pytest.mark.parametrize("pre", [MatchState.PROPOSED, MatchState.A_ACCEPTED])
def test_other_side_cannot_tell_consent_close_from_decline(pre) -> None:
    """A's view after B withdrew consent equals A's view after B declined."""
    closed, declined = reach(pre), reach(pre)
    closed.close_for_consent(Side.B, LATER)
    apply(declined, Side.B, Action.DECLINE, LATER)
    for t in (LATER, EXPIRES_AT + timedelta(days=1)):
        assert closed.status_for(A_ID, t) is declined.status_for(A_ID, t)


# ---------------------------------------------------------------- status_for masking


def test_status_for_basic_table() -> None:
    p = make_proposal()
    assert p.status_for(A_ID, NOW) is ViewerStatus.AWAITING_YOU
    apply(p, Side.A, Action.ACCEPT)
    assert p.status_for(A_ID, NOW) is ViewerStatus.WAITING_ON_THEM
    assert p.status_for(B_ID, NOW) is ViewerStatus.AWAITING_YOU  # B is not told A said yes (Q2)
    apply(p, Side.B, Action.ACCEPT, LATER)
    assert p.status_for(A_ID, LATER) is ViewerStatus.MUTUAL
    assert p.status_for(B_ID, LATER) is ViewerStatus.MUTUAL


def test_status_for_declined_by_you_vs_other() -> None:
    p = make_proposal()
    apply(p, Side.B, Action.DECLINE)
    assert p.status_for(B_ID, NOW) is ViewerStatus.DECLINED_BY_YOU
    assert p.status_for(A_ID, NOW) is ViewerStatus.AWAITING_YOU  # the decline is invisible


def test_status_for_other_decline_after_my_accept_looks_like_waiting() -> None:
    p = reach(MatchState.A_ACCEPTED)
    apply(p, Side.B, Action.DECLINE, LATER)
    assert p.status_for(A_ID, LATER) is ViewerStatus.WAITING_ON_THEM


def test_status_for_expired_by_clock_even_if_not_swept() -> None:
    p = reach(MatchState.A_ACCEPTED)
    assert p.status_for(A_ID, EXPIRES_AT) is ViewerStatus.EXPIRED
    assert p.status_for(B_ID, EXPIRES_AT) is ViewerStatus.EXPIRED


def test_status_for_mutual_never_expires() -> None:
    p = reach(MatchState.MUTUAL)
    assert p.status_for(A_ID, EXPIRES_AT + timedelta(days=100)) is ViewerStatus.MUTUAL


# ---------------------------------------------------------------- non-participants


def test_non_participant_gets_404_everywhere() -> None:
    p = make_proposal()
    for fn in (
        lambda: p.decide(STRANGER, Action.ACCEPT, NOW),
        lambda: p.status_for(STRANGER, NOW),
        lambda: p.side_of(STRANGER),
    ):
        exc = error_of(fn)
        assert exc.http_status == 404
    assert p.side_of(A_ID) is Side.A and p.side_of(B_ID) is Side.B
    assert VIEWER[Side.A] == A_ID
