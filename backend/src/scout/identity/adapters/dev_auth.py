"""Dev auth (D16): a local ES256 key pair, a token issuer and a verifier. Never allowed when APP_ENV=prod."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from scout.identity.adapters.jwks import SupabaseJwksVerifier
from scout.identity.ports.auth import AuthPrincipal
from scout.shared import Clock

KID = "dev-1"
TOKEN_TTL = timedelta(hours=1)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


class DevKeyPair:
    """ES256 key pair persisted under ``var/dev-keys`` (git-ignored, files mode 0600)."""

    def __init__(self, private: ec.EllipticCurvePrivateKey) -> None:
        self.private = private

    @classmethod
    def load_or_create(cls, directory: Path) -> DevKeyPair:
        """Load ``private.pem`` or generate it (and ``public.jwk``)."""
        directory.mkdir(parents=True, exist_ok=True)
        pem = directory / "private.pem"
        if pem.exists():
            key = serialization.load_pem_private_key(pem.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise ValueError("dev key is not an EC key")
            pair = cls(key)
        else:
            pair = cls(ec.generate_private_key(ec.SECP256R1()))
            _write_private(
                pem,
                pair.private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
            )
        _write_private(directory / "public.jwk", json.dumps(pair.jwks()).encode())
        return pair

    def jwks(self) -> dict[str, Any]:
        """Public key set."""
        jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        return {"keys": [{**jwk, "kid": KID, "alg": "ES256", "use": "sig"}]}


class DevTokenIssuer:
    """Issue ES256 access tokens for an e-mail address (``sub`` is a deterministic uuid5 of the e-mail)."""

    def __init__(self, keys: DevKeyPair, issuer: str, clock: Clock) -> None:
        self._keys, self._issuer, self._clock = keys, issuer, clock

    @staticmethod
    def sub_for(email: str) -> str:
        """Deterministic user id for an e-mail."""
        return str(uuid5(NAMESPACE_URL, f"scout-dev:{email.strip().lower()}"))

    def issue(self, email: str) -> str:
        """Return a signed token valid for one hour."""
        now = self._clock.now()
        payload = {
            "sub": self.sub_for(email), "email": email.strip().lower(), "role": "authenticated",
            "aud": "authenticated", "iss": self._issuer,
            "iat": int(now.timestamp()), "exp": int((now + TOKEN_TTL).timestamp()),
        }  # fmt: skip
        return jwt.encode(payload, self._keys.private, algorithm="ES256", headers={"kid": KID})


class DevVerifier:
    """Verifier trusting the local dev public key."""

    def __init__(self, keys: DevKeyPair, issuer: str, clock: Clock) -> None:
        async def fetch() -> dict[str, Any]:
            return keys.jwks()

        self._inner = SupabaseJwksVerifier(fetch, issuer, clock)

    async def verify(self, bearer_token: str) -> AuthPrincipal:
        """Delegate to the JWKS verifier."""
        return await self._inner.verify(bearer_token)
