"""``/system`` endpoints: what the UI needs to explain a pause."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from scout.api.deps import current_account, get_container
from scout.identity.domain.account import Account
from scout.ports.llm import ModelTier

CONFIDENTIAL_NOTICE = (
    "No approved AI provider is configured for confidential data, so Scout works from your published work only. "
    "Uploaded documents are read on our server but not sent to any AI model, so they are not analysed until the "
    "operator approves a provider."
)
PARTIAL_NOTICE = (
    "The operator has approved an AI provider for reading your documents, but not for every analysis step, so "
    "analyses and matches still use your published work only."
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
        description="False unless an approved language-model provider covers every step (both the fast and the "
        "reasoning tier): analyses and matching then use published data only. Documents are read on the server "
        "either way and are analysed as soon as the fast tier is approved.",
    )
    confidential_data_notice: str | None = Field(
        default=None,
        description="Plain-language explanation shown when `confidential_data_enabled` is false.",
    )
    vault_upload_warning: bool = Field(
        default=True,
        description="True when the UI must warn users not to upload confidential material and require a tick "
        "before uploading (setting `VAULT_UPLOAD_WARNING`).",
    )


@router.get("/status", summary="Are new analyses accepted right now?", response_model=SystemStatusOut)
async def system_status(request: Request, account: Account = Depends(current_account)) -> SystemStatusOut:  # noqa: B008
    """The shared daily budget is a pilot safeguard. When it is used up, runs resume after midnight UTC."""
    c = get_container(request)
    st = await c.global_budget.status()
    enabled = c.llm.confidential_allowed()  # every tier: analyses and matching need both
    documents_analysed = c.llm.confidential_allowed(ModelTier.FAST)  # what ingestion needs
    notice = None if enabled else PARTIAL_NOTICE if documents_analysed else CONFIDENTIAL_NOTICE
    warning = c.settings.vault_upload_warning
    if st.paused:
        return SystemStatusOut(
            accepting_runs=False,
            message="Scout has reached its daily analysis limit. New analyses open again tomorrow (UTC).",
            confidential_data_enabled=enabled,
            confidential_data_notice=notice,
            vault_upload_warning=warning,
        )
    return SystemStatusOut(
        accepting_runs=True,
        confidential_data_enabled=enabled,
        confidential_data_notice=notice,
        vault_upload_warning=warning,
    )
