"""``/matches`` endpoints: anonymous proposals, double opt-in decisions, reveal after both accept."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from scout.api.deps import current_account, get_container
from scout.api.feedback import feedback_out
from scout.api.schemas.feedback import FeedbackOut, MatchRatingIn
from scout.api.schemas.matching import DecisionIn, MatchCardOut, MatchListOut
from scout.feedback.application.give_feedback import UNSET
from scout.feedback.domain.feedback import Reason, TargetType
from scout.identity.domain.account import Account
from scout.matching.domain.enums import Action

router = APIRouter(prefix="/api/v1", tags=["matching"])


@router.get("/matches", summary="Your match proposals", response_model=MatchListOut)
async def list_matches(
    request: Request,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=50),
    account: Account = Depends(current_account),  # noqa: B008
) -> MatchListOut:
    """Newest first. Identity is shown only for mutual matches; a decline is never shown to the other side."""
    page = await get_container(request).matching.list(account.id, cursor, limit)
    return MatchListOut(
        matching_enabled=page.matching_enabled, items=page.items, next_cursor=page.next_cursor
    )


@router.get(
    "/matches/{match_id}",
    summary="One match",
    response_model=MatchCardOut,
    responses={404: {"description": "Not yours, unknown or hidden."}},
)
async def get_match(
    match_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> MatchCardOut:
    """Anonymous card, or the revealed view with contact and joint ideas once both accepted."""
    return await get_container(request).matching.get(account.id, match_id)


@router.post(
    "/matches/{match_id}/decision",
    summary="Accept, decline or withdraw",
    response_model=MatchCardOut,
    responses={
        404: {"description": "Not yours or unknown."},
        409: {"description": "Expired, closed, nothing to withdraw, or consent required."},
    },
)
async def decide(
    match_id: UUID,
    body: DecisionIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> MatchCardOut:
    """Both sides must accept before names and contact details appear; withdraw takes an acceptance back."""
    return await get_container(request).decide_match.execute(
        account.id, match_id, Action(body.action), body.never_similar
    )


@router.post(
    "/matches/{match_id}/rating",
    summary="Rate a mutual match",
    response_model=FeedbackOut,
    responses={404: {"description": "Not a mutual match of yours."}},
)
async def rate_match(
    match_id: UUID,
    body: MatchRatingIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> FeedbackOut:
    """1 to 5 stars with optional reason chips; rating again replaces the earlier rating."""
    fb = await get_container(request).give_feedback.execute(
        account.id,
        TargetType.MATCH,
        match_id,
        None,
        None if body.reasons is None else [Reason(r) for r in body.reasons],
        body.comment if "comment" in body.model_fields_set else UNSET,
        body.stars,
    )
    return feedback_out(fb)
