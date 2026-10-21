"""Builders shared by the matching unit tests.

Assumed signatures (the implementer may adapt ONLY this file for the shapes of ScoreBasis, StoredRationale and
PublicFacet, which the design note leaves open; everything else in the tests is the contract):

* ``scout.matching.domain.proposal``: ``MatchProposal``, ``ScoreBasis``, ``StoredRationale``, ``PublicFacet``
* ``MatchProposal.propose(*, id, a_id, b_id, field, score, score_basis, rationale, a_public, b_public, now,
  expiry_days) -> MatchProposal`` (records ``MatchProposed``; raises ``InvariantViolation`` for a_id >= b_id or
  score outside [0, 1]).
* ``StoredRationale(source="template")`` must be constructible with every other field defaulted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from scout.matching.domain.enums import Action, CareerBand, Region, Side
from scout.matching.domain.ids import match_id
from scout.matching.domain.proposal import MatchProposal, PublicFacet, ScoreBasis, StoredRationale

from scout.shared import AccountId, ScoutError

A_ID = AccountId(UUID(int=1))
B_ID = AccountId(UUID(int=2))
STRANGER = AccountId(UUID(int=99))
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
EXPIRY_DAYS = 14
EXPIRES_AT = NOW + timedelta(days=EXPIRY_DAYS)
VIEWER = {Side.A: A_ID, Side.B: B_ID}


def make_proposal(*, score: float = 0.5, a_id: AccountId = A_ID, b_id: AccountId = B_ID) -> MatchProposal:
    """Return a fresh PROPOSED match created at ``NOW``."""
    return MatchProposal.propose(
        id=match_id(a_id, b_id, "2026-W43"),
        a_id=a_id,
        b_id=b_id,
        field="bio-based-materials",
        score=score,
        score_basis=ScoreBasis(
            topic_overlap=0.4,
            method_overlap=0.2,
            activity=0.75,
            alpha=1.0,
            beta=1.5,
            shared_topics=["t1"],
            a_only=["m1"],
            b_only=["m2"],
            formula="c1",
        ),
        rationale=StoredRationale(source="template"),
        a_public=PublicFacet(region=Region.EUROPE, career_band=CareerBand.MID),
        b_public=PublicFacet(region=Region.ASIA, career_band=CareerBand.EARLY),
        now=NOW,
        expiry_days=EXPIRY_DAYS,
    )


def apply(p: MatchProposal, side: Side, action: Action, now: datetime = NOW, never_similar: bool = False):
    """Run ``decide`` for ``side`` and return the events."""
    return p.decide(VIEWER[side], action, now, never_similar=never_similar)


def error_of(fn) -> ScoutError:
    """Call ``fn`` and return the ScoutError it raises (fails the test if none)."""
    try:
        fn()
    except ScoutError as exc:
        return exc
    raise AssertionError("expected a ScoutError")
