"""Verify Supabase (or dev) access tokens against a JWKS: asymmetric algorithms only."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import jwt

from scout.identity.ports.auth import AuthPrincipal, InvalidToken
from scout.shared import Clock

ALLOWED_ALGS = frozenset({"ES256", "RS256", "EdDSA"})
JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


class HttpJwksFetcher:
    """Fetch a JWKS document over HTTPS."""

    def __init__(self, url: str, http: httpx.AsyncClient | None = None) -> None:
        self._url, self._http = url, http

    async def __call__(self) -> dict[str, Any]:
        """GET the JWKS."""
        if self._http is not None:
            r = await self._http.get(self._url)
        else:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self._url)
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data


class SupabaseJwksVerifier:
    """Implements ``AuthTokenVerifier`` (asymmetric signature, exp, iss, role, sub)."""

    def __init__(self, fetch: JwksFetcher, issuer: str, clock: Clock, cache_seconds: int = 600) -> None:
        self._fetch, self._issuer, self._clock = fetch, issuer, clock
        self._ttl = timedelta(seconds=min(cache_seconds, 600))
        self._keys: dict[str, dict[str, Any]] = {}
        self._fetched_at: datetime | None = None

    async def _refresh(self) -> None:
        try:
            doc = await self._fetch()
        except Exception as exc:
            raise InvalidToken("Unable to load signing keys") from exc
        self._keys = {k["kid"]: k for k in doc.get("keys", []) if "kid" in k}
        self._fetched_at = self._clock.now()

    async def _key_for(self, kid: str) -> Any:
        now = self._clock.now()
        if self._fetched_at is None or now - self._fetched_at > self._ttl:
            await self._refresh()
        if kid not in self._keys:
            await self._refresh()  # unknown kid: key rotation, refetch once
        jwk = self._keys.get(kid)
        if jwk is None:
            raise InvalidToken("Unknown signing key")
        try:
            return jwt.PyJWK(jwk).key
        except Exception as exc:
            raise InvalidToken("Unusable signing key") from exc

    async def verify(self, bearer_token: str) -> AuthPrincipal:
        """Return the principal or raise ``InvalidToken``."""
        try:
            header = jwt.get_unverified_header(bearer_token)
        except jwt.PyJWTError as exc:
            raise InvalidToken() from exc
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGS or not isinstance(kid, str):
            raise InvalidToken()
        key = await self._key_for(kid)
        try:
            claims = jwt.decode(
                bearer_token,
                key,
                algorithms=[alg],
                issuer=self._issuer,
                options={"verify_exp": False, "verify_aud": False, "verify_iat": False, "verify_nbf": False,
                         "require": ["exp", "sub", "iss"]},
            )  # fmt: skip
        except jwt.PyJWTError as exc:
            raise InvalidToken() from exc
        exp = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
        if exp <= self._clock.now():
            raise InvalidToken("Token expired")
        if claims.get("role") != "authenticated":
            raise InvalidToken()
        try:
            sub = UUID(str(claims["sub"]))
        except ValueError as exc:
            raise InvalidToken() from exc
        meta = claims.get("app_metadata") or {}
        return AuthPrincipal(
            sub=sub, email=claims.get("email"), role="authenticated",
            app_role=str(meta.get("role", "user")) if isinstance(meta, dict) else "user", exp=exp,
        )  # fmt: skip
