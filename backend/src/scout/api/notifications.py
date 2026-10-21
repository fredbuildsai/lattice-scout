"""``/me/notifications``: e-mail preferences."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from scout.api.deps import current_account, get_container
from scout.identity.domain.account import Account
from scout.notifications.domain.notification import Prefs

router = APIRouter(prefix="/api/v1/me/notifications", tags=["notifications"])


class NotificationPrefsOut(BaseModel):
    """What Scout may e-mail."""

    email_runs: bool = Field(description="A mail when an analysis finishes.")
    email_matches: bool = Field(description="A mail for a new suggestion and for a mutual match.")
    email_digest: bool = Field(description="A weekly summary (off by default).")


class NotificationPrefsIn(BaseModel):
    """Partial update."""

    model_config = ConfigDict(extra="forbid")

    email_runs: bool | None = None
    email_matches: bool | None = None
    email_digest: bool | None = None


def _out(p: Prefs) -> NotificationPrefsOut:
    return NotificationPrefsOut(
        email_runs=p.email_runs, email_matches=p.email_matches, email_digest=p.email_digest
    )


@router.get("", summary="E-mail preferences", response_model=NotificationPrefsOut)
async def get_prefs(request: Request, account: Account = Depends(current_account)) -> NotificationPrefsOut:  # noqa: B008
    """Defaults: runs and matches on, digest off."""
    return _out(await get_container(request).notifications.get_prefs(account.id))


@router.patch("", summary="Change e-mail preferences", response_model=NotificationPrefsOut)
async def patch_prefs(
    body: NotificationPrefsIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> NotificationPrefsOut:
    """Only the fields you send change."""
    n = get_container(request).notifications
    return _out(
        await n.update_prefs(
            account.id, runs=body.email_runs, matches=body.email_matches, digest=body.email_digest
        )
    )
