"""``/system`` endpoints: what the UI needs to explain a pause."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from scout.api.deps import current_account, get_container
from scout.identity.domain.account import Account

CONFIDENTIAL_NOTICE = (
    "No approved provider is configured, so Scout works from your published work only. Uploaded documents are "
    "stored but not read until the operator approves a provider."
)

router = APIRouter(prefix="/api/v1/system", tags=["system"])


class SystemStatusOut(BaseModel):
    """Whether new analyses are accepted right now."""

    accepting_runs: bool = Field(description="False when a global daily spend cap is reached.")
    message: str | None = Field(
        default=None, description="Plain-language reason, shown by the UI when paused."
    )
    confidential_data_enabled: bool = Field(
        default=True,
        description="False when no approved language-model provider is configured: analyses, matching and "
        "document processing then use published data only (uploaded documents are kept but not read).",
    )
    confidential_data_notice: str | None = Field(
        default=None,
        description="Plain-language explanation shown when `confidential_data_enabled` is false.",
    )


@router.get("/status", summary="Are new analyses accepted right now?", response_model=SystemStatusOut)
async def system_status(request: Request, account: Account = Depends(current_account)) -> SystemStatusOut:  # noqa: B008
    """The shared daily budget is a pilot safeguard. When it is used up, runs resume after midnight UTC."""
    c = get_container(request)
    st = await c.global_budget.status()
    enabled = c.llm.confidential_allowed()
    notice = None if enabled else CONFIDENTIAL_NOTICE
    if st.paused:
        return SystemStatusOut(
            accepting_runs=False,
            message="Scout has reached its daily analysis limit. New analyses open again tomorrow (UTC).",
            confidential_data_enabled=enabled,
            confidential_data_notice=notice,
        )
    return SystemStatusOut(
        accepting_runs=True, confidential_data_enabled=enabled, confidential_data_notice=notice
    )
