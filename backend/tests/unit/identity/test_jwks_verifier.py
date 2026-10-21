"""M1.1 SupabaseJwksVerifier: ES256 verification, rejection of HS256/none, JWKS caching.

Covers IMPLEMENTATION_PLAN M1 backend task 1 acceptance ("Invalid, expired or HS256-signed
tokens get 401") and ARCHITECTURE section 10 (Auth row), decision D16.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes.jwt import ISSUER, SUB, FakeJwks, TestKey, claims, forge

from scout.identity.adapters.jwks import SupabaseJwksVerifier
from scout.identity.ports.auth import InvalidToken
from scout.shared import InfrastructureError
from scout.shared.clock import FixedClock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def key() -> TestKey:
    return TestKey("k1")


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def jwks(key: TestKey) -> FakeJwks:
    return FakeJwks(key)


@pytest.fixture
def verifier(jwks: FakeJwks, clock: FixedClock) -> SupabaseJwksVerifier:
    return SupabaseJwksVerifier(jwks, ISSUER, clock)


async def test_valid_token_yields_principal(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    p = await verifier.verify(key.sign(claims(NOW)))
    assert p.sub == UUID(SUB) and p.email == "ada@example.edu" and p.role == "authenticated"
    assert p.exp == datetime.fromtimestamp(int(NOW.timestamp()) + 3600, UTC)


async def test_expired_rejected(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(claims(NOW, ttl=-5)))


async def test_expiry_is_judged_by_injected_clock(
    verifier: SupabaseJwksVerifier, key: TestKey, clock: FixedClock
) -> None:
    tok = key.sign(claims(NOW, ttl=60))
    await verifier.verify(tok)
    clock.advance(timedelta(minutes=5))
    with pytest.raises(InvalidToken):
        await verifier.verify(tok)


async def test_wrong_issuer_rejected(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(claims(NOW, iss="https://evil.example/auth/v1")))


@pytest.mark.parametrize("role", ["anon", "service_role", "admin"])
async def test_wrong_role_rejected(verifier: SupabaseJwksVerifier, key: TestKey, role: str) -> None:
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(claims(NOW, role=role)))


async def test_missing_role_rejected(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    c = claims(NOW)
    del c["role"]
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(c))


@pytest.mark.parametrize("sub", ["not-a-uuid", "12345", ""])
async def test_sub_must_be_uuid(verifier: SupabaseJwksVerifier, key: TestKey, sub: str) -> None:
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(claims(NOW, sub=sub)))


async def test_missing_sub_rejected(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    c = claims(NOW)
    del c["sub"]
    with pytest.raises(InvalidToken):
        await verifier.verify(key.sign(c))


@pytest.mark.parametrize("secret_kind", ["public_pem", "arbitrary"])
async def test_hs256_rejected(verifier: SupabaseJwksVerifier, key: TestKey, secret_kind: str) -> None:
    secret = key.public_pem if secret_kind == "public_pem" else b"super-secret"
    tok = forge({"alg": "HS256", "typ": "JWT", "kid": key.kid}, claims(NOW), secret)
    with pytest.raises(InvalidToken):
        await verifier.verify(tok)


async def test_hs256_without_kid_rejected(verifier: SupabaseJwksVerifier, key: TestKey) -> None:
    tok = forge({"alg": "HS256", "typ": "JWT"}, claims(NOW), b"super-secret")
    with pytest.raises(InvalidToken):
        await verifier.verify(tok)


@pytest.mark.parametrize("alg", ["none", "None", "NONE"])
async def test_alg_none_rejected(verifier: SupabaseJwksVerifier, key: TestKey, alg: str) -> None:
    tok = forge({"alg": alg, "typ": "JWT", "kid": key.kid}, claims(NOW))
    with pytest.raises(InvalidToken):
        await verifier.verify(tok)


async def test_token_signed_by_other_key_same_kid_rejected(verifier: SupabaseJwksVerifier) -> None:
    other = TestKey("k1")  # same kid, different key material
    with pytest.raises(InvalidToken):
        await verifier.verify(other.sign(claims(NOW)))


async def test_unknown_kid_refetch_is_throttled(
    verifier: SupabaseJwksVerifier, jwks: FakeJwks, clock: FixedClock
) -> None:
    stranger = TestKey("unknown")
    for _ in range(5):
        with pytest.raises(InvalidToken):
            await verifier.verify(stranger.sign(claims(NOW)))
    assert jwks.calls == 1  # initial load only: garbage tokens cannot hammer the JWKS endpoint
    clock.advance(timedelta(seconds=31))
    with pytest.raises(InvalidToken):
        await verifier.verify(stranger.sign(claims(clock.now())))
    assert jwks.calls == 2


async def test_unknown_kid_accepted_after_key_rotation(
    verifier: SupabaseJwksVerifier, jwks: FakeJwks, key: TestKey, clock: FixedClock
) -> None:
    await verifier.verify(key.sign(claims(NOW)))
    new = TestKey("k2")
    jwks.keys.append(new)
    clock.advance(timedelta(seconds=31))  # past the unknown-kid cooldown
    assert (await verifier.verify(new.sign(claims(clock.now())))).sub == UUID(SUB)
    assert jwks.calls == 2


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "a.b", "....", "Bearer x", " "])
async def test_malformed_rejected(verifier: SupabaseJwksVerifier, token: str) -> None:
    with pytest.raises(InvalidToken):
        await verifier.verify(token)


async def test_jwks_cached_within_ten_minutes_and_refetched_after(
    verifier: SupabaseJwksVerifier, jwks: FakeJwks, key: TestKey, clock: FixedClock
) -> None:
    def tok() -> str:
        return key.sign(claims(clock.now(), ttl=7200))

    await verifier.verify(tok())
    await verifier.verify(tok())
    assert jwks.calls == 1
    clock.advance(timedelta(minutes=9))
    await verifier.verify(tok())
    assert jwks.calls == 1
    clock.advance(timedelta(minutes=2))  # 11 minutes since first fetch
    await verifier.verify(tok())
    assert jwks.calls == 2


async def test_cache_seconds_is_configurable(jwks: FakeJwks, key: TestKey, clock: FixedClock) -> None:
    v = SupabaseJwksVerifier(jwks, ISSUER, clock, cache_seconds=30)
    await v.verify(key.sign(claims(NOW)))
    clock.advance(timedelta(seconds=31))
    await v.verify(key.sign(claims(clock.now())))
    assert jwks.calls == 2


async def test_jwks_outage_without_keys_is_503_not_401(clock: FixedClock, key: TestKey) -> None:
    async def boom() -> dict[str, object]:
        raise ConnectionError("down")

    v = SupabaseJwksVerifier(boom, ISSUER, clock)
    with pytest.raises(InfrastructureError):
        await v.verify(key.sign(claims(NOW)))


async def test_jwks_outage_serves_stale_keys_within_grace(clock: FixedClock, key: TestKey) -> None:
    ok = FakeJwks(key)
    state = {"down": False}

    async def fetch() -> dict[str, object]:
        if state["down"]:
            raise ConnectionError("down")
        return await ok()

    v = SupabaseJwksVerifier(fetch, ISSUER, clock)
    await v.verify(key.sign(claims(NOW, ttl=86400)))
    state["down"] = True
    clock.advance(timedelta(minutes=11))  # cache expired, refetch fails, stale keys still serve
    assert (await v.verify(key.sign(claims(clock.now(), ttl=3600)))).sub == UUID(SUB)
    clock.advance(timedelta(hours=2))  # beyond the grace period
    with pytest.raises(InfrastructureError):
        await v.verify(key.sign(claims(clock.now(), ttl=3600)))


async def test_outage_after_ttl_costs_one_fetch_per_cooldown_not_one_per_request(
    clock: FixedClock, key: TestKey
) -> None:
    ok, state = FakeJwks(key), {"down": False, "calls": 0}

    async def fetch() -> dict[str, object]:
        state["calls"] += 1
        if state["down"]:
            raise ConnectionError("down")
        return await ok()

    v = SupabaseJwksVerifier(fetch, ISSUER, clock)
    await v.verify(key.sign(claims(NOW, ttl=86400)))
    state["down"] = True
    clock.advance(timedelta(minutes=11))
    for _ in range(20):
        await v.verify(key.sign(claims(clock.now(), ttl=3600)))
    assert state["calls"] == 2  # initial load + one failed refresh within the cooldown window
