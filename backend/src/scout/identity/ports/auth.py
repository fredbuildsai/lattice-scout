"""Port for verifying bearer tokens."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from scout.shared import Unauthenticated


class AuthPrincipal(BaseModel):
    """Identity extracted from a verified access token."""

    model_config = ConfigDict(frozen=True)

    sub: UUID
    email: str | None = None
    role: str = "authenticated"
    app_role: str = "user"
    exp: AwareDatetime


class InvalidToken(Unauthenticated):
    """The token is missing, malformed, expired, wrongly signed or has the wrong issuer or role."""

    code = "invalid_token"
    title = "Invalid or expired token"


class AuthTokenVerifier(Protocol):
    """Verify a bearer token and return the principal."""

    async def verify(self, bearer_token: str) -> AuthPrincipal:
        """Return the principal or raise ``InvalidToken``."""
        ...
