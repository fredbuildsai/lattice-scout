"""FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from scout.bootstrap import Container
from scout.identity.domain.account import Account
from scout.identity.ports.auth import AuthPrincipal, InvalidToken
from scout.shared.context import update_context


def get_container(request: Request) -> Container:
    """The application container."""
    container: Container = request.app.state.container
    return container


async def current_principal(request: Request) -> AuthPrincipal:
    """Verify the ``Authorization: Bearer`` header and return the principal (401 otherwise)."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidToken()
    principal = await get_container(request).auth_verifier.verify(token.strip())
    update_context(account_id=principal.sub)
    return principal


async def current_account(request: Request) -> Account:
    """Authenticated principal -> account (created on the first call, e-mail synced)."""
    principal = await current_principal(request)
    return await get_container(request).identity.ensure_account(
        principal.sub, principal.email, principal.issued_at
    )
