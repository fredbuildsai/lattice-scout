"""``/opportunities`` and ``/evidence`` endpoints. Only verified claims and citations are returned."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from scout.api.deps import current_account, get_container
from scout.api.schemas.discovery import (
    ChecklistItemOut,
    ClaimOut,
    EvidenceOpenOut,
    EvidenceOut,
    NoveltyOut,
    OpportunityCardOut,
    OpportunityDetailOut,
    OpportunityListOut,
    OpportunityPatchIn,
    ScoresOut,
    basis_note_for,
    basis_of,
)
from scout.discovery.application.service import CardView, ClaimView, DetailView
from scout.discovery.domain.enums import OppKind, OppStatus
from scout.identity.domain.account import Account

router = APIRouter(prefix="/api/v1", tags=["discovery"])


def _claim(c: ClaimView) -> ClaimOut:
    return ClaimOut(id=c.id, text=c.text, markers=[EvidenceOut(**m.__dict__) for m in c.markers])


def card_out(v: CardView) -> OpportunityCardOut:
    """Map a card view to its DTO (``one_line`` and unverified material are never included)."""
    o = v.opportunity
    s = o.scores
    basis = o.score_basis
    return OpportunityCardOut(
        id=o.id, run_id=o.run_id, kind=o.kind.value, title=o.title,
        why=_claim(v.why) if v.why else None,
        scores=ScoresOut(fit=s.fit, novelty=s.novelty, readiness=s.readiness, effort=s.effort) if s else None,
        novelty=NoveltyOut(count=basis.get("count"), since_year=basis.get("since_year"), query=basis.get("query"), band=basis.get("band")),
        status=o.status.value, verified_sources=v.verified_sources, clusters=v.clusters, created_at=o.created_at,
        basis=basis_of(basis.get("basis")), basis_note=basis_note_for(basis.get("basis"), bool(basis.get("documents", 1))),
    )  # fmt: skip


def detail_out(d: DetailView) -> OpportunityDetailOut:
    """Map a detail view to its DTO."""
    o = d.card.opportunity
    return OpportunityDetailOut(
        **card_out(d.card).model_dump(),
        rationale=[_claim(c) for c in d.rationale], objection=[_claim(c) for c in d.objection],
        score_claims=[_claim(c) for c in d.score], next_steps=list(o.next_steps),
        readiness=[ChecklistItemOut(item=str(x.get("item", "")), present=bool(x.get("present_in_vault"))) for x in o.readiness_checklist],
        effort_estimate=o.effort_estimate,
    )  # fmt: skip


@router.get(
    "/opportunities",
    summary="Your opportunities",
    response_model=OpportunityListOut,
    responses={404: {"description": "Not your run."}},
)
async def list_opportunities(
    request: Request,
    run_id: UUID | None = None,
    status: Literal["shown", "saved", "dismissed"] | None = None,
    kind: Literal["white_spot", "synergy", "resubmission"] | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=50),
    account: Account = Depends(current_account),  # noqa: B008
) -> OpportunityListOut:
    """Shown and saved by default, best rank first. Dismissed ones only with `status=dismissed`."""
    page = await get_container(request).discovery.list_opportunities(
        account.id, run_id=run_id, status=OppStatus(status) if status else None,
        kind=OppKind(kind) if kind else None, cursor=cursor, limit=limit,
    )  # fmt: skip
    return OpportunityListOut(items=[card_out(c) for c in page.items], next_cursor=page.next_cursor)


@router.get(
    "/opportunities/{opportunity_id}",
    summary="One opportunity with its evidence",
    response_model=OpportunityDetailOut,
    responses={404: {"description": "Not yours, or not visible."}},
)
async def get_opportunity(
    opportunity_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> OpportunityDetailOut:
    """Verified claims only, each with its verified citations."""
    return detail_out(await get_container(request).discovery.get_opportunity(account.id, opportunity_id))


@router.patch(
    "/opportunities/{opportunity_id}",
    summary="Save, dismiss or restore an opportunity",
    response_model=OpportunityCardOut,
    responses={404: {"description": "Not yours, or not visible."}},
)
async def patch_opportunity(
    opportunity_id: UUID,
    body: OpportunityPatchIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> OpportunityCardOut:
    """Any order among `shown`, `saved` and `dismissed`."""
    card = await get_container(request).discovery.set_status(
        account.id, opportunity_id, OppStatus(body.status)
    )
    return card_out(card)


@router.get(
    "/evidence/{evidence_id}/open",
    summary="Open the source behind a citation",
    response_model=EvidenceOpenOut,
    responses={404: {"description": "Not yours, not visible, or the source is gone."}},
)
async def open_evidence(
    evidence_id: UUID,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> EvidenceOpenOut:
    """Vault passages are read live: a deleted document answers 404; under `discard` only the quote is returned."""
    e = await get_container(request).discovery.open_evidence(account.id, evidence_id)
    return EvidenceOpenOut(**e.__dict__)
