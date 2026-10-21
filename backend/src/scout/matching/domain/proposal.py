"""``MatchProposal``: the double opt-in state machine. Whatever one side did, the other side observes the same."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

from scout.matching.domain.enums import (
    Action,
    CareerBand,
    CloseReason,
    Decision,
    IdeasStatus,
    MatchState,
    Region,
    Side,
    ViewerStatus,
)
from scout.matching.domain.ids import MATCHING_NS
from scout.shared import AccountId, AggregateRoot, Conflict, DomainEvent, InvariantViolation, NotFound

PENDING = (MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED)


class MatchExpired(Conflict):
    """The proposal ran out."""

    code = "match_expired"
    title = "Match expired"


class MatchClosed(Conflict):
    """The proposal is closed for this action."""

    code = "match_closed"
    title = "Match closed"


class NothingToWithdraw(Conflict):
    """The viewer has no acceptance to withdraw."""

    code = "nothing_to_withdraw"
    title = "Nothing to withdraw"


class MatchNotFound(NotFound):
    """Not a participant, or unknown."""

    code = "match_not_found"
    title = "Match not found"


class NeverSimilarNeedsDecline(InvariantViolation):
    """``never_similar`` only goes with a decline."""

    code = "never_similar_needs_decline"
    title = "never_similar requires decline"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BringItem(_Frozen):
    """One thing a side brings."""

    text: str = Field(max_length=120)
    source_refs: list[str] = Field(default_factory=list, max_length=3)


class StoredRationale(_Frozen):
    """The explanation stored with the proposal (public data only)."""

    source: Literal["llm", "template"]
    you_bring: list[BringItem] = Field(default_factory=list, max_length=3)  # from side A's point of view
    they_bring: list[BringItem] = Field(default_factory=list, max_length=3)
    why: str = Field(default="", max_length=300)
    prompt_version: str | None = None

    def without(self, side: Side) -> StoredRationale:
        """Forget ``side``: its bring-list and the shared ``why`` go, and so does every ``source_refs``."""
        keep = self.they_bring if side is Side.A else self.you_bring
        keep = [BringItem(text=i.text) for i in keep]
        you, they = ([], keep) if side is Side.A else (keep, [])
        return self.model_copy(update={"you_bring": you, "they_bring": they, "why": ""})


class ScoreBasis(_Frozen):
    """Numbers behind a score."""

    topic_overlap: float
    method_overlap: float
    activity: float
    alpha: float
    beta: float
    shared_topics: list[str] = Field(default_factory=list)
    a_only: list[str] = Field(default_factory=list)
    b_only: list[str] = Field(default_factory=list)
    formula: str = "c1"


class PublicFacet(_Frozen):
    """The only data about a person readable before the match is mutual (snapshotted at creation)."""

    region: Region = Region.UNKNOWN
    career_band: CareerBand = CareerBand.UNKNOWN


class MatchProposed(DomainEvent):
    """A proposal was created."""

    name = "matching.proposed"
    match_id: UUID
    a_id: UUID
    b_id: UUID


class MatchBecameMutual(DomainEvent):
    """Both sides accepted. There is deliberately no decline event."""

    name = "matching.mutual"
    match_id: UUID
    a_id: UUID
    b_id: UUID


def _event_id(match: UUID, name: str) -> UUID:
    return uuid5(MATCHING_NS, f"{match}:{name}")


@dataclass
class MatchProposal(AggregateRoot):
    """A proposed collaboration between two researchers (``a_id < b_id``)."""

    id: UUID
    a_id: AccountId | None
    b_id: AccountId | None
    field: str
    score: float
    score_basis: ScoreBasis
    rationale: StoredRationale
    a_public: PublicFacet
    b_public: PublicFacet
    created_at: datetime
    expires_at: datetime
    state: MatchState = MatchState.PROPOSED
    a_decision: Decision | None = None
    b_decision: Decision | None = None
    a_decided_at: datetime | None = None
    b_decided_at: datetime | None = None
    declined_by: Side | None = None
    close_reason: CloseReason | None = None
    never_similar_by: Side | None = None
    revealed_at: datetime | None = None
    closed_at: datetime | None = None
    ideas_status: IdeasStatus = IdeasStatus.NONE

    @classmethod
    def propose(
        cls, *, id: UUID, a_id: AccountId, b_id: AccountId, field: str, score: float, score_basis: ScoreBasis,
        rationale: StoredRationale, a_public: PublicFacet, b_public: PublicFacet, now: datetime, expiry_days: int,
    ) -> MatchProposal:  # fmt: skip
        """Create a proposal.

        Raises:
            InvariantViolation: ``a_id >= b_id`` or the score is outside [0, 1].
        """
        if not a_id < b_id:
            raise InvariantViolation("ids_not_ordered")
        if not (isinstance(score, float | int) and not math.isnan(score) and 0.0 <= score <= 1.0):
            raise InvariantViolation("score_out_of_range")
        p = cls(
            id=id, a_id=a_id, b_id=b_id, field=field, score=float(score), score_basis=score_basis,
            rationale=rationale, a_public=a_public, b_public=b_public, created_at=now,
            expires_at=now + timedelta(days=expiry_days),
        )  # fmt: skip
        p.record_event(
            MatchProposed(
                event_id=_event_id(id, "proposed"), occurred_at=now, match_id=id, a_id=a_id, b_id=b_id
            )
        )
        return p

    # ---- helpers -----------------------------------------------------------------------------------------------
    def side_of(self, viewer: AccountId) -> Side:
        """Side of a participant.

        Raises:
            MatchNotFound: ``viewer`` is not a participant.
        """
        if viewer == self.a_id:
            return Side.A
        if viewer == self.b_id:
            return Side.B
        raise MatchNotFound()

    def decision_of(self, side: Side) -> Decision | None:
        """Stored decision of ``side``."""
        return self.a_decision if side is Side.A else self.b_decision

    def _set(self, side: Side, decision: Decision | None, now: datetime) -> None:
        if side is Side.A:
            self.a_decision, self.a_decided_at = decision, now if decision else None
        else:
            self.b_decision, self.b_decided_at = decision, now if decision else None

    def _decline(self, side: Side, now: datetime, never_similar: bool, reason: CloseReason) -> None:
        self._set(side, Decision.DECLINE, now)
        self.state, self.declined_by, self.close_reason, self.closed_at = (
            MatchState.DECLINED,
            side,
            reason,
            now,
        )
        if never_similar:
            self.never_similar_by = side

    def expire(self, now: datetime) -> bool:
        """Move a pending proposal to ``expired`` once its time is up. True when it changed."""
        if self.state in PENDING and now >= self.expires_at:
            self.state, self.closed_at = MatchState.EXPIRED, now
            return True
        return False

    # ---- transitions -------------------------------------------------------------------------------------------
    def decide(
        self, viewer: AccountId, action: Action, now: datetime, never_similar: bool = False
    ) -> list[DomainEvent]:
        """Apply ``action`` for ``viewer``; returns the events to publish.

        After the other side declined, the viewer's actions are recorded silently and answered exactly as if the
        other side had never decided.

        Raises:
            MatchNotFound: not a participant.
            NeverSimilarNeedsDecline: ``never_similar`` without a decline.
            MatchExpired, MatchClosed, NothingToWithdraw: the action does not fit the state.
        """
        side = self.side_of(viewer)
        if never_similar and action is not Action.DECLINE:
            raise NeverSimilarNeedsDecline()
        own = self.decision_of(side)
        if self.state is MatchState.MUTUAL:
            if action is Action.ACCEPT:
                return []
            raise MatchClosed()
        if self.state is MatchState.EXPIRED:
            raise MatchExpired()
        if self.state is MatchState.DECLINED:
            return self._decide_after_decline(side, own, action, now, never_similar)
        if self.expire(now):
            raise MatchExpired()
        if action is Action.ACCEPT:
            if own is Decision.ACCEPT:
                return []
            self._set(side, Decision.ACCEPT, now)
            if self.decision_of(side.other) is Decision.ACCEPT:
                self.state, self.revealed_at = MatchState.MUTUAL, now
                return [
                    MatchBecameMutual(
                        event_id=_event_id(self.id, "mutual"), occurred_at=now, match_id=self.id,
                        a_id=self.a_id, b_id=self.b_id,
                    )
                ]  # fmt: skip
            self.state = MatchState.A_ACCEPTED if side is Side.A else MatchState.B_ACCEPTED
            return []
        if action is Action.DECLINE:
            self._decline(side, now, never_similar, CloseReason.DECLINED)
            return []
        if own is not Decision.ACCEPT:
            raise NothingToWithdraw()
        self._set(side, None, now)
        self.state = MatchState.PROPOSED
        return []

    def _decide_after_decline(
        self, side: Side, own: Decision | None, action: Action, now: datetime, never_similar: bool
    ) -> list[DomainEvent]:
        """The viewer's world when the proposal is declined: the other side's decline never changes an answer."""
        if self.declined_by is side:
            if action is Action.DECLINE:
                return []
            raise MatchClosed()
        if own is Decision.DECLINE:  # silently declined: closed for this viewer
            if action is Action.DECLINE:
                return []
            raise MatchClosed()
        if now >= self.expires_at:
            raise MatchExpired()
        if action is Action.ACCEPT:
            self._set(side, Decision.ACCEPT, now)
        elif action is Action.DECLINE:
            self._set(side, Decision.DECLINE, now)
            if never_similar:
                self.never_similar_by = side
        elif own is Decision.ACCEPT:
            self._set(side, None, now)
        else:
            raise NothingToWithdraw()
        return []

    def close_for_consent(self, side: Side, now: datetime) -> None:
        """A side withdrew its matching consent: decline a pending proposal. Looks like a decline to the other."""
        if self.state in PENDING:
            self._decline(side, now, False, CloseReason.CONSENT_WITHDRAWN)

    def detach(self, side: Side, now: datetime) -> None:
        """The account of ``side`` is being erased: close like a consent close, then forget the side.

        The counterpart sees the same thing as after ``close_for_consent``. Mutual proposals are deleted by the
        caller instead.

        Raises:
            InvariantViolation: the proposal is mutual.
        """
        if self.state is MatchState.MUTUAL:
            raise InvariantViolation("detach_mutual")
        if self.state in PENDING:
            self._decline(side, now, False, CloseReason.ACCOUNT_DELETED)
        if side is Side.A:
            self.a_id, self.a_decision, self.a_decided_at = None, None, None
        else:
            self.b_id, self.b_decision, self.b_decided_at = None, None, None
        if self.never_similar_by is side:
            self.never_similar_by = None
        self.rationale = self.rationale.without(side)

    # ---- read --------------------------------------------------------------------------------------------------
    def status_for(self, viewer: AccountId, now: datetime) -> ViewerStatus:
        """What ``viewer`` is told. The other side's decision is never an input (only ``mutual`` is visible)."""
        side = self.side_of(viewer)
        own = self.decision_of(side)
        if self.state is MatchState.MUTUAL:
            return ViewerStatus.MUTUAL
        if self.declined_by is side or own is Decision.DECLINE:
            return ViewerStatus.DECLINED_BY_YOU
        if self.state is MatchState.EXPIRED or now >= self.expires_at:
            return ViewerStatus.EXPIRED
        return ViewerStatus.WAITING_ON_THEM if own is Decision.ACCEPT else ViewerStatus.AWAITING_YOU
