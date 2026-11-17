"""``/matches`` endpoints: anonymous proposals, double opt-in decisions, reveal after both accept."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from scout.api.deps import current_account, get_container
from scout.api.feedback import feedback_out
from scout.api.schemas.feedback import FeedbackOut, MatchRatingIn
from scout.api.schemas.matching import (
    DecisionIn,
    ExternalLayerName,
    ExternalListOut,
    ExternalRefreshIn,
    ExternalStateIn,
    ExternalSuggestionOut,
    MatchCardOut,
    MatchListOut,
)
from scout.feedback.application.give_feedback import UNSET
from scout.feedback.domain.feedback import Reason, TargetType
from scout.identity.domain.account import Account
from scout.matching.domain.enums import Action
from scout.matching.domain.external import ExternalLayer, ExternalSuggestion, SuggestionState

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


def external_out(s: ExternalSuggestion) -> ExternalSuggestionOut:
    """Domain suggestion -> DTO."""
    return ExternalSuggestionOut(
        id=s.id, openalex_id=s.openalex_id, openalex_url=f"https://openalex.org/{s.openalex_id}", name=s.name,
        institution=s.institution, layer=s.layer.value, vias=list(s.vias), shared_topics=list(s.shared_topics),
        score=s.score, topic_overlap=s.topic_overlap, formula=s.formula, h_index=s.h_index, citations=s.citations,
        works_count=s.works_count, recent_works=s.recent_works, state=s.state.value, created_at=s.created_at,
    )  # fmt: skip


@router.get(
    "/matches/external",
    summary="Researchers not on Lattice Scout yet",
    response_model=ExternalListOut,
)
async def list_external(
    request: Request,
    layer: ExternalLayerName | None = None,
    account: Account = Depends(current_account),  # noqa: B008
) -> ExternalListOut:
    """Active and saved suggestions from public OpenAlex data; dismissed ones are hidden. These people are not
    members: they are never contacted and never enter the double opt-in."""
    items = await get_container(request).external_suggestions.visible(
        account.id, ExternalLayer(layer) if layer else None
    )
    return ExternalListOut(items=[external_out(s) for s in items])


@router.post(
    "/matches/external/refresh",
    summary="Find researchers not on Lattice Scout yet",
    response_model=ExternalListOut,
    responses={
        409: {"description": "Claim your profile first."},
        429: {"description": "Daily search limit reached (EXTERNAL_REFRESH_PER_DAY)."},
    },
)
async def refresh_external(
    body: ExternalRefreshIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> ExternalListOut:
    """Searches OpenAlex now (in_network: co-authors of your co-authors; in_field: outside your network, same
    topics), stores new suggestions and returns the list. No LLM is involved."""
    items = await get_container(request).external_suggestions.refresh(
        account.id, [ExternalLayer(x) for x in dict.fromkeys(body.scopes)]
    )
    return ExternalListOut(items=[external_out(s) for s in items])


@router.patch(
    "/matches/external/{suggestion_id}",
    summary="Save, dismiss or restore a suggestion",
    response_model=ExternalSuggestionOut,
    responses={404: {"description": "Not yours or unknown."}},
)
async def set_external_state(
    suggestion_id: UUID,
    body: ExternalStateIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> ExternalSuggestionOut:
    """``saved`` keeps it at hand, ``dismissed`` hides it (it is never suggested again), ``active`` restores it."""
    s = await get_container(request).external_suggestions.set_state(
        account.id, suggestion_id, SuggestionState(body.state)
    )
    return external_out(s)


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
