"""``/me`` endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from scout.api.deps import current_account, get_container
from scout.api.schemas.profile import ConsentIn, MeOut, ResearcherSummaryOut
from scout.identity.domain.account import Account
from scout.ports.jobs import JobSpec

router = APIRouter(prefix="/api/v1", tags=["identity"])


async def _me(request: Request, account: Account) -> MeOut:
    r = await get_container(request).profile.summary(account.id)
    return MeOut(
        account_id=account.id, email=account.email, role=account.role.value, launch_field=account.launch_field,
        consent_matching=account.consent_matching, consent_vault_signals=account.consent_vault_signals,
        created_at=account.created_at,
        researcher=ResearcherSummaryOut(
            researcher_id=r.id, display_name=r.display_name, verified_level=r.public_level().value,
            import_status=r.import_status.value,
        )
        if r
        else None,
    )  # fmt: skip


@router.get("/me", summary="Current account, consents and profile summary", response_model=MeOut)
async def get_me(request: Request, account: Account = Depends(current_account)) -> MeOut:  # noqa: B008
    """Return the signed-in account; creates it on the first call."""
    return await _me(request, account)


@router.patch("/me/consent", summary="Update matching and vault-signal consents", response_model=MeOut)
async def patch_consent(
    body: ConsentIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> MeOut:
    """Turning matching off also turns vault signals off; vault signals need matching."""
    c = get_container(request)
    updated = await c.identity.update_consent(account.id, body.consent_matching, body.consent_vault_signals)
    await c.job_queue.enqueue(
        JobSpec(
            kind="matching.profile",
            payload={"account_id": str(account.id)},
            dedupe_key=f"profile:{account.id}",
            max_attempts=3,
        )
    )
    return await _me(request, updated)
