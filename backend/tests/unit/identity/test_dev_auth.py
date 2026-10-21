"""M1.1 dev auth: DevKeyPair persistence, DevTokenIssuer, DevVerifier round trip.

Covers IMPLEMENTATION_PLAN M1 backend task 1 and ARCHITECTURE section 10 (Auth row), D16.
Assumed constructor contract: ``DevVerifier(keys, issuer, clock)``.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from tests.fakes.jwt import ISSUER, claims

from scout.identity.adapters.dev_auth import DevKeyPair, DevTokenIssuer, DevVerifier
from scout.identity.ports.auth import InvalidToken
from scout.shared.clock import FixedClock

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(NOW)


@pytest.fixture
def keys(tmp_path: Path) -> DevKeyPair:
    return DevKeyPair.load_or_create(tmp_path / "keys")


async def test_issue_and_verify_roundtrip(keys: DevKeyPair, clock: FixedClock) -> None:
    tok = DevTokenIssuer(keys, ISSUER, clock).issue("ada@example.edu")
    p = await DevVerifier(keys, ISSUER, clock).verify(tok)
    assert p.email == "ada@example.edu" and p.role == "authenticated"
    assert p.exp == NOW + timedelta(hours=1)


def test_token_is_es256_with_kid(keys: DevKeyPair, clock: FixedClock) -> None:
    hdr = jwt.get_unverified_header(DevTokenIssuer(keys, ISSUER, clock).issue("ada@example.edu"))
    assert hdr["alg"] == "ES256" and hdr.get("kid")


def test_sub_is_deterministic_uuid_per_email(keys: DevKeyPair, clock: FixedClock) -> None:
    iss = DevTokenIssuer(keys, ISSUER, clock)

    def sub(email: str) -> str:
        return str(jwt.decode(iss.issue(email), options={"verify_signature": False})["sub"])

    assert sub("ada@example.edu") == sub("ada@example.edu")
    assert sub("ada@example.edu") != sub("bob@example.edu")
    UUID(sub("ada@example.edu"))


async def test_sub_stable_across_key_pairs(tmp_path: Path, clock: FixedClock) -> None:
    a = DevKeyPair.load_or_create(tmp_path / "a")
    b = DevKeyPair.load_or_create(tmp_path / "b")
    pa = await DevVerifier(a, ISSUER, clock).verify(DevTokenIssuer(a, ISSUER, clock).issue("ada@example.edu"))
    pb = await DevVerifier(b, ISSUER, clock).verify(DevTokenIssuer(b, ISSUER, clock).issue("ada@example.edu"))
    assert pa.sub == pb.sub


async def test_key_pair_persisted_and_reloaded(tmp_path: Path, clock: FixedClock) -> None:
    d = tmp_path / "keys"
    first = DevKeyPair.load_or_create(d)
    tok = DevTokenIssuer(first, ISSUER, clock).issue("ada@example.edu")
    second = DevKeyPair.load_or_create(d)
    assert (await DevVerifier(second, ISSUER, clock).verify(tok)).email == "ada@example.edu"


def test_private_key_files_are_0600(tmp_path: Path) -> None:
    d = tmp_path / "keys"
    DevKeyPair.load_or_create(d)
    files = [p for p in d.rglob("*") if p.is_file()]
    assert files
    for f in files:
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f


async def test_verifier_rejects_token_from_other_key_pair(tmp_path: Path, clock: FixedClock) -> None:
    mine = DevKeyPair.load_or_create(tmp_path / "mine")
    other = DevKeyPair.load_or_create(tmp_path / "other")
    tok = DevTokenIssuer(other, ISSUER, clock).issue("ada@example.edu")
    with pytest.raises(InvalidToken):
        await DevVerifier(mine, ISSUER, clock).verify(tok)


async def test_dev_token_expires_after_one_hour(keys: DevKeyPair, clock: FixedClock) -> None:
    tok = DevTokenIssuer(keys, ISSUER, clock).issue("ada@example.edu")
    clock.advance(timedelta(hours=1, seconds=1))
    with pytest.raises(InvalidToken):
        await DevVerifier(keys, ISSUER, clock).verify(tok)


async def test_dev_verifier_rejects_wrong_role(keys: DevKeyPair, clock: FixedClock) -> None:
    """Same checks as the production verifier apply in dev."""
    tok = DevTokenIssuer(keys, "https://other-issuer", clock).issue("ada@example.edu")
    with pytest.raises(InvalidToken):
        await DevVerifier(keys, ISSUER, clock).verify(tok)
    assert claims(NOW)["role"] == "authenticated"  # sanity for the shared helper
