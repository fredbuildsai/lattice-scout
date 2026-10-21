"""The reveal rule and the two views of a match. Identity fields exist only in ``RevealedMatchView``."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from scout.matching.domain.enums import CareerBand, Decision, IdeasStatus, MatchState, Region, ViewerStatus
from scout.matching.domain.proposal import BringItem, MatchProposal
from scout.shared import AccountId, InvariantViolation


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BringText(_Frozen):
    """What the other side brings, without references (they would point at that person's papers)."""

    text: str


class Contact(_Frozen):
    """Identity and contact of the other side. Only built for a mutual match."""

    display_name: str
    institution: str | None
    email: str
    orcid: str | None
    openalex_ids: list[str]
    region: Region
    career_band: CareerBand
    # Read together with the contact, shown on ``RevealedMatchView``; not part of the contact payload.
    career_since: int | None = Field(default=None, exclude=True)
    joint_works_count: int = Field(default=0, exclude=True)


class JointIdeaView(_Frozen):
    """One collaboration idea, from the viewer's point of view."""

    title: str
    question: str
    uses_from_you: list[BringItem]
    uses_from_them: list[BringItem]
    first_step: str
    possible_output: str


class AnonymousMatchView(_Frozen):
    """A proposal before the reveal: no name, no institution, no year."""

    kind: Literal["anonymous"] = "anonymous"
    id: UUID
    status: ViewerStatus
    score: float
    field_label: str
    region: Region
    career_band: CareerBand
    shared_topics: list[str]
    you_bring: list[BringItem]
    they_bring: list[BringText]
    why: str
    my_decision: Decision | None
    created_at: datetime
    expires_at: datetime


class RevealedMatchView(_Frozen):
    """A mutual match: identity, contact and joint ideas.

    ``career_since`` is the year of the other side's first included work; ``joint_works_count`` the works both
    included (None when none). Neither exists on ``AnonymousMatchView``.
    """

    kind: Literal["revealed"] = "revealed"
    id: UUID
    status: Literal[ViewerStatus.MUTUAL] = ViewerStatus.MUTUAL
    score: float
    shared_topics: list[str]
    you_bring: list[BringItem]
    they_bring: list[BringItem]
    why: str
    contact: Contact
    ideas_status: IdeasStatus
    joint_ideas: list[JointIdeaView]
    revealed_at: datetime
    my_rating: int | None = None
    career_since: int | None = None
    joint_works_count: int | None = None


def reveal_allowed(state: MatchState) -> bool:
    """Identity may be shown only when the match is mutual."""
    return state is MatchState.MUTUAL


def _perspective(p: MatchProposal, viewer: AccountId) -> tuple[list[BringItem], list[BringItem]]:
    r = p.rationale
    return (r.you_bring, r.they_bring) if viewer == p.a_id else (r.they_bring, r.you_bring)


def build_anonymous(
    p: MatchProposal, viewer: AccountId, now: datetime, field_label: str
) -> AnonymousMatchView:
    """The anonymous card for ``viewer`` (the other side's public facet only)."""
    side = p.side_of(viewer)
    mine, theirs = _perspective(p, viewer)
    other = p.b_public if viewer == p.a_id else p.a_public
    return AnonymousMatchView(
        id=p.id, status=p.status_for(viewer, now), score=p.score, field_label=field_label,
        region=other.region, career_band=other.career_band, shared_topics=list(p.score_basis.shared_topics),
        you_bring=mine, they_bring=[BringText(text=i.text) for i in theirs], why=p.rationale.why_for(side),
        my_decision=p.decision_of(side), created_at=p.created_at, expires_at=p.expires_at,
    )  # fmt: skip


def build_revealed(
    p: MatchProposal,
    viewer: AccountId,
    contact: Contact,
    ideas: Sequence[JointIdeaView],
    my_rating: int | None = None,
) -> RevealedMatchView:
    """The revealed view.

    Raises:
        InvariantViolation: the match is not mutual (``reveal_before_mutual``).
    """
    if not reveal_allowed(p.state) or p.revealed_at is None:
        raise InvariantViolation("reveal_before_mutual")
    side = p.side_of(viewer)
    mine, theirs = _perspective(p, viewer)
    return RevealedMatchView(
        id=p.id, score=p.score, shared_topics=list(p.score_basis.shared_topics), you_bring=mine, they_bring=theirs,
        why=p.rationale.why_for(side), contact=contact, ideas_status=p.ideas_status, joint_ideas=list(ideas),
        revealed_at=p.revealed_at, my_rating=my_rating, career_since=contact.career_since,
        joint_works_count=contact.joint_works_count or None,
    )  # fmt: skip
