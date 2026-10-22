"""Auth provider admin calls: remove a user from Supabase Auth (or do nothing in dev)."""

from __future__ import annotations

from uuid import UUID

import httpx

from scout.shared import InfrastructureError


class DevAuthAdmin:
    """``AUTH_MODE=dev``: tokens are self-issued, so there is no provider account to remove."""

    async def delete_user(self, user_id: UUID) -> None:
        """No-op."""


class SupabaseAuthAdmin:
    """``DELETE {SUPABASE_URL}/auth/v1/admin/users/{id}`` with the service-role key. Not run from the VM."""

    def __init__(self, http: httpx.AsyncClient, base_url: str, service_key: str) -> None:
        self._http = http
        self._url = base_url.rstrip("/") + "/auth/v1/admin/users"
        self._headers = {"Authorization": f"Bearer {service_key}", "apikey": service_key}

    async def delete_user(self, user_id: UUID) -> None:
        """Delete the user; 404 counts as success.

        Raises:
            InfrastructureError: the provider is unreachable or refuses.
        """
        try:
            r = await self._http.delete(f"{self._url}/{user_id}", headers=self._headers, timeout=15)
        except httpx.HTTPError as exc:
            raise InfrastructureError("Auth provider unavailable") from exc
        if r.status_code >= 400 and r.status_code != 404:
            raise InfrastructureError(f"Auth provider refused the delete ({r.status_code})")
