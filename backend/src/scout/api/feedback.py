"""``/feedback`` endpoints: thumbs and reason chips on opportunities (and matches from M5)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from scout.api.deps import current_account, get_container
from scout.api.schemas.feedback import FeedbackIn, FeedbackOut
from scout.feedback.application.give_feedback import UNSET
from scout.feedback.domain.feedback import Feedback, Rating, Reason, TargetType
from scout.identity.domain.account import Account
from scout.shared import NotFound

router = APIRouter(prefix="/api/v1", tags=["feedback"])


def feedback_out(f: Feedback) -> FeedbackOut:
    return FeedbackOut(
        target_type=f.target_type.value, target_id=f.target_id, rating=f.rating.value if f.rating else None, stars=f.stars,
        reasons=[r.value for r in f.reasons], comment=f.comment, updated_at=f.updated_at,
    )  # fmt: skip


@router.post(
    "/feedback",
    summary="Rate an opportunity",
    response_model=FeedbackOut,
    responses={404: {"description": "Not yours, not visible, or not rateable yet."}},
)
async def give_feedback(
    body: FeedbackIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> FeedbackOut:
    """Thumbs up or down with optional reason chips and a short comment; the latest rating replaces the earlier one."""
    fb = await get_container(request).give_feedback.execute(
        account.id, TargetType(body.target_type), body.target_id, Rating(body.rating) if body.rating else None,
        None if body.reasons is None else [Reason(r) for r in body.reasons],
        body.comment if "comment" in body.model_fields_set else UNSET, body.stars,
    )  # fmt: skip
    return feedback_out(fb)


@router.get(
    "/feedback",
    summary="Your rating of a target",
    response_model=FeedbackOut,
    responses={404: {"description": "No rating yet."}},
)
async def my_feedback(
    target_type: Literal["opportunity", "match"],
    target_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> FeedbackOut:
    """What you rated earlier, so the thumbs show their state."""
    fb = await get_container(request).give_feedback.mine(account.id, TargetType(target_type), target_id)
    if fb is None:
        raise NotFound("no_feedback")
    return feedback_out(fb)
