"""``/admin`` endpoints (quality review of claims). Admin role required."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from scout.api.deps import current_account, get_container
from scout.api.schemas.discovery import AdminClaimListOut, AdminClaimOut, AdminEvidenceOut
from scout.discovery.application.service import AdminClaimView
from scout.discovery.domain.enums import ClaimStatus, SourceType
from scout.identity.domain.account import Account, AccountRole
from scout.shared import Forbidden

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


def _row(v: AdminClaimView, show_vault_quotes: bool) -> AdminClaimOut:
    o, c = v.opportunity, v.claim
    return AdminClaimOut(
        claim_id=c.id, opportunity_id=o.id, run_id=o.run_id, section=c.section.value, text=c.text,
        status=c.status.value, status_reason=c.status_reason, set_by=c.set_by.value, agent=c.agent,
        prompt_version=c.prompt_version, model=c.model, critic_verdict=c.critic_verdict,
        critic_reason=c.critic_reason, opportunity_status=o.status.value,
        evidence=[
            AdminEvidenceOut(
                source_type=e.source_type.value, label=e.label, ref=e.ref, verified=e.verified,
                check=e.check.value if e.check else None,
                quote=None if (e.source_type is SourceType.VAULT and not show_vault_quotes) else e.quote,
            )
            for e in c.evidence
        ],
    )  # fmt: skip


@router.get(
    "/claims",
    summary="Review claims of every status",
    response_model=AdminClaimListOut,
    responses={403: {"description": "Admin role required."}},
)
async def list_claims(
    request: Request,
    status: ClaimStatus | None = None,
    reason: str | None = None,
    agent: str | None = None,
    prompt_version: str | None = None,
    model: str | None = None,
    run_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=50),
    account: Account = Depends(current_account),  # noqa: B008
) -> AdminClaimListOut:
    """Vault quotes are hidden unless `ADMIN_CLAIMS_SHOW_VAULT_QUOTES=true`. Each call is audited."""
    if account.role is not AccountRole.ADMIN:
        raise Forbidden("Admin role required")
    svc = get_container(request).discovery
    page = await svc.admin_claims(
        account.id, status=status, reason=reason, agent=agent, prompt_version=prompt_version, model=model,
        run_id=run_id, cursor=cursor, limit=limit,
    )  # fmt: skip
    return AdminClaimListOut(
        items=[_row(v, svc.admin_shows_vault_quotes) for v in page.items], next_cursor=page.next_cursor
    )
