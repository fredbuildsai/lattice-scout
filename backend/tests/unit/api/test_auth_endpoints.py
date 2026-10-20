"""M1.1 HTTP auth: current_principal dependency (401 problem+json) and POST /api/v1/dev/login.

Covers IMPLEMENTATION_PLAN M1 backend task 1 acceptance ("Invalid, expired or HS256-signed
tokens get 401") and ARCHITECTURE section 10 (Auth row), D16.
Assumptions: Container gains ``auth_verifier``; the dev router lives under ``/api/v1`` like the
other routers; the container wires a DevVerifier when auth_mode == "dev".
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from tests.fakes.jwt import ISSUER, FakeJwks, TestKey, claims, forge

from scout.api.app import create_app
from scout.api.deps import current_principal
from scout.bootstrap import build_container
from scout.identity.adapters.jwks import SupabaseJwksVerifier
from scout.identity.ports.auth import AuthPrincipal
from scout.settings import Settings
from scout.shared.clock import FixedClock

LOGIN = "/api/v1/dev/login"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _with_protected_route(app: FastAPI) -> None:
    @app.get("/_test/whoami")
    async def whoami(p: AuthPrincipal = Depends(current_principal)) -> dict[str, str]:  # noqa: B008
        return {"sub": str(p.sub)}


@pytest.fixture
def key() -> TestKey:
    return TestKey("k1")


@pytest.fixture
def client(key: TestKey) -> Iterator[TestClient]:
    """App whose verifier trusts our local key (fixed clock)."""
    c = build_container(Settings(_env_file=None, app_env="test", auth_mode="supabase",
                                 supabase_url="https://proj.supabase.co"))  # fmt: skip
    c = replace(c, auth_verifier=SupabaseJwksVerifier(FakeJwks(key), ISSUER, FixedClock(NOW)))
    app = create_app(c)
    _with_protected_route(app)
    with TestClient(app) as cl:
        yield cl


def _assert_401(r) -> None:  # type: ignore[no-untyped-def]
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "invalid_token"
    assert r.headers["www-authenticate"].lower().startswith("bearer")


def test_no_header_is_401(client: TestClient) -> None:
    _assert_401(client.get("/_test/whoami"))


@pytest.mark.parametrize("header", ["garbage", "Bearer", "Bearer garbage", "Basic abc", "Bearer a.b.c"])
def test_bad_header_is_401(client: TestClient, header: str) -> None:
    _assert_401(client.get("/_test/whoami", headers={"Authorization": header}))


def test_expired_is_401(client: TestClient, key: TestKey) -> None:
    tok = key.sign(claims(NOW, ttl=-60))
    _assert_401(client.get("/_test/whoami", headers={"Authorization": f"Bearer {tok}"}))


def test_hs256_is_401(client: TestClient, key: TestKey) -> None:
    tok = forge({"alg": "HS256", "typ": "JWT", "kid": key.kid}, claims(NOW), key.public_pem)
    _assert_401(client.get("/_test/whoami", headers={"Authorization": f"Bearer {tok}"}))


def test_valid_token_is_200(client: TestClient, key: TestKey) -> None:
    tok = key.sign(claims(NOW))
    r = client.get("/_test/whoami", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["sub"] == claims(NOW)["sub"]


def test_401_body_does_not_leak_token(client: TestClient) -> None:
    r = client.get("/_test/whoami", headers={"Authorization": "Bearer SECRETTOKENVALUE"})
    assert "SECRETTOKENVALUE" not in r.text


@pytest.fixture
def dev_client(tmp_path, monkeypatch) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DEV_KEYS_DIR", str(tmp_path / "keys"))
    app = create_app(build_container(Settings(_env_file=None, app_env="test", auth_mode="dev")))
    _with_protected_route(app)
    with TestClient(app) as cl:
        yield cl


def test_dev_login_then_protected_route(dev_client: TestClient) -> None:
    r = dev_client.post(LOGIN, json={"email": "ada@example.edu"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"].lower() == "bearer" and body["expires_in"] == 3600 and body["access_token"]
    ok = dev_client.get("/_test/whoami", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert ok.status_code == 200
    again = dev_client.post(LOGIN, json={"email": "ada@example.edu"}).json()["access_token"]
    ok2 = dev_client.get("/_test/whoami", headers={"Authorization": f"Bearer {again}"})
    assert ok2.json()["sub"] == ok.json()["sub"]


def test_dev_login_rejects_invalid_email(dev_client: TestClient) -> None:
    assert dev_client.post(LOGIN, json={"email": "not-an-email"}).status_code == 422


def test_dev_login_not_mounted_when_auth_mode_supabase(client: TestClient) -> None:
    assert client.post(LOGIN, json={"email": "ada@example.edu"}).status_code == 404
