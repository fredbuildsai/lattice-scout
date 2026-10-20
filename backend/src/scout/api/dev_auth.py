"""Development-only login: ``POST /api/v1/dev/login``. Mounted only when AUTH_MODE=dev."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from scout.api.deps import get_container
from scout.identity.adapters.dev_auth import TOKEN_TTL

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


class DevLoginRequest(BaseModel):
    """Body of the dev login."""

    email: str = Field(
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254, description="Any e-mail address."
    )


class DevLoginResponse(BaseModel):
    """Access token for the dev auth mode."""

    access_token: str = Field(description="ES256 JWT signed with the local dev key.")
    token_type: str = Field(default="bearer", description="Always bearer.")
    expires_in: int = Field(description="Seconds until the token expires.")


@router.post("/login", summary="Dev login: issue an access token for an e-mail (AUTH_MODE=dev only)")
async def dev_login(body: DevLoginRequest, request: Request) -> DevLoginResponse:
    """Return a token without any credential check. Refused at startup when APP_ENV=prod."""
    c = get_container(request)
    issuer = c.dev_tokens
    assert issuer is not None  # router is mounted only in dev mode
    return DevLoginResponse(
        access_token=issuer.issue(body.email),
        token_type="bearer",  # noqa: S106
        expires_in=int(TOKEN_TTL.total_seconds()),
    )
