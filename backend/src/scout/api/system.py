"""``/system`` endpoints: what the UI needs to explain a pause."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from scout.api.deps import current_account, get_container
from scout.identity.domain.account import Account

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class SystemStatusOut(BaseModel):
    """Whether new analyses are accepted right now."""

    accepting_runs: bool = Field(description="False when a global daily spend cap is reached.")
    message: str | None = Field(
        default=None, description="Plain-language reason, shown by the UI when paused."
    )


@router.get("/status", summary="Are new analyses accepted right now?", response_model=SystemStatusOut)
async def system_status(request: Request, account: Account = Depends(current_account)) -> SystemStatusOut:  # noqa: B008
    """The shared daily budget is a pilot safeguard. When it is used up, runs resume after midnight UTC."""
    st = await get_container(request).global_budget.status()
    if st.paused:
        return SystemStatusOut(
            accepting_runs=False,
            message="Scout has reached its daily analysis limit. New analyses open again tomorrow (UTC).",
        )
    return SystemStatusOut(accepting_runs=True)
