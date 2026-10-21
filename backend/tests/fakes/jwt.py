"""Test helpers: locally signed JWTs and a counting fake JWKS fetcher (no network)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ISSUER = "https://proj.supabase.co/auth/v1"
SUB = "6f1c2b0e-3a4d-4f5e-8a9b-0c1d2e3f4a5b"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class TestKey:
    """An ES256 key pair with a kid."""

    __test__ = False

    def __init__(self, kid: str = "k1") -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())
        self.public_pem = self.private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def jwk(self) -> dict[str, Any]:
        """Public JWK as published in a JWKS."""
        d = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(self.private.public_key()))
        return {**d, "kid": self.kid, "alg": "ES256", "use": "sig"}

    def sign(self, payload: dict[str, Any], *, kid: str | None = None) -> str:
        """Return an ES256 token."""
        return jwt.encode(payload, self.private, algorithm="ES256", headers={"kid": kid or self.kid})


def claims(
    now: datetime, *, sub: str = SUB, iss: str = ISSUER, role: str = "authenticated",
    ttl: int = 3600, email: str | None = "ada@example.edu",
) -> dict[str, Any]:  # fmt: skip
    """Standard Supabase-like claims relative to ``now`` (negative ttl = already expired)."""
    out: dict[str, Any] = {
        "sub": sub, "iss": iss, "role": role, "aud": "authenticated",
        "iat": int(now.timestamp()) - 10, "exp": int(now.timestamp()) + ttl,
    }  # fmt: skip
    if email:
        out["email"] = email
    return out


def forge(header: dict[str, Any], payload: dict[str, Any], secret: bytes | None = None) -> str:
    """Hand-build a token (HS256 with ``secret`` or alg none) bypassing PyJWT safety checks."""
    h = _b64(json.dumps(header).encode())
    p = _b64(json.dumps(payload).encode())
    signing_input = f"{h}.{p}".encode()
    sig = b"" if secret is None else hmac.new(secret, signing_input, hashlib.sha256).digest()
    return f"{h}.{p}.{_b64(sig)}"


class FakeJwks:
    """Async JWKS fetcher that counts calls and can swap its key set."""

    def __init__(self, *keys: TestKey) -> None:
        self.keys = list(keys)
        self.calls = 0

    async def __call__(self) -> dict[str, Any]:
        self.calls += 1
        return {"keys": [k.jwk() for k in self.keys]}
