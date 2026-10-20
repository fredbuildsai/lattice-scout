"""M1 acceptance (plan M1): sign in (dev auth), find 'Johan Foster', merge the split record, import, land with the
profile saved; token and ownership rules; consents. Runs against PostgreSQL with the real worker handlers."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tests.conftest import TEST_URL

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration
FOSTER, SPLIT, MOREIRA = "A5059588273", "A5140167166", "A5033675494"


def _settings() -> Settings:
    return Settings(_env_file=None, app_env="test", database_url=TEST_URL)


@pytest.fixture
def client(db: object) -> Iterator[TestClient]:
    with TestClient(create_app(build_container(_settings()))) as c:
        yield c


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _run_worker() -> int:
    """Process all due jobs with a separate container (own event loop)."""

    async def go() -> int:
        c = build_container(_settings())
        handlers = build_handlers(c)
        n = 0
        while await run_once(c.job_queue, handlers, "test-worker"):
            n += 1
        await c.db.dispose()
        return n

    return asyncio.run(go())


def test_acceptance_find_merge_import_and_land(client: TestClient) -> None:
    h = _login(client, "ada@example.edu")
    me = client.get("/api/v1/me", headers=h).json()
    assert me["email"] == "ada@example.edu" and me["researcher"] is None and not me["consent_matching"]

    cand = client.get(
        "/api/v1/profile/candidates",
        params={"name": "Johan Foster", "affiliation": "British Columbia"},
        headers=h,
    )
    assert cand.status_code == 200
    g = cand.json()["groups"][0]
    assert g["group_id"] == FOSTER and g["includes_records"] == 2 and g["primary"]["works_count"] == 159
    assert {r["author_id"] for r in g["records"]} == {FOSTER, SPLIT}

    r = client.post(
        "/api/v1/profile/claim", json={"author_ids": [FOSTER, SPLIT], "primary_author_id": FOSTER}, headers=h
    )
    assert r.status_code == 202 and r.json()["import"]["status"] == "queued"
    assert (
        client.post(
            "/api/v1/profile/claim",
            json={"author_ids": [SPLIT, FOSTER], "primary_author_id": FOSTER},
            headers=h,
        ).json()["import"]["generation"]
        == 1
    )

    assert _run_worker() == 1  # exactly one import job
    p = client.get("/api/v1/profile", headers=h).json()
    assert p["import"]["status"] == "done" and p["verified_level"] == "orcid_matched"
    assert (
        p["works_summary"]["total"] == 159
        and p["works_summary"]["included"] == 149
        and p["works_summary"]["uncertain"] == 10
    )
    assert p["institution"] == "University of British Columbia" and p["topics"][0]["weight"] == 1.0
    assert [x["author_id"] for x in p["openalex_ids"]] == [FOSTER, SPLIT]
    assert client.get("/api/v1/me", headers=h).json()["researcher"]["import_status"] == "done"

    work = next(w for w in p["works"] if w["included"])
    patched = client.patch(f"/api/v1/profile/works/{work['id']}", json={"included": False}, headers=h)
    assert patched.status_code == 200 and patched.json()["included_by_user"] is True

    other = _login(client, "bob@example.edu")
    assert (
        client.patch(
            f"/api/v1/profile/works/{work['id']}", json={"included": True}, headers=other
        ).status_code
        == 404
    )
    assert client.get("/api/v1/profile", headers=other).status_code == 404


def test_tokens_are_enforced(client: TestClient) -> None:
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/profile/candidates", params={"name": "x y"}).status_code == 401


def test_consent_rules_and_validation(client: TestClient) -> None:
    h = _login(client, "ada@example.edu")
    ok = client.patch(
        "/api/v1/me/consent", json={"consent_matching": True, "consent_vault_signals": True}, headers=h
    )
    assert ok.status_code == 200 and ok.json()["consent_vault_signals"] is True
    off = client.patch("/api/v1/me/consent", json={"consent_matching": False}, headers=h).json()
    assert off["consent_matching"] is False and off["consent_vault_signals"] is False
    bad = client.patch("/api/v1/me/consent", json={"consent_vault_signals": True}, headers=h)
    assert bad.status_code == 422 and bad.json()["code"] == "consent_requires_matching"
    assert client.patch("/api/v1/me/consent", json={}, headers=h).status_code == 422
    assert client.patch("/api/v1/me/consent", json={"nope": 1}, headers=h).status_code == 422


def test_unknown_author_and_conflicting_orcids(client: TestClient) -> None:
    h = _login(client, "ada@example.edu")
    r = client.post("/api/v1/profile/claim", json={"author_ids": ["A1"]}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "unknown_author"
    r = client.post("/api/v1/profile/claim", json={"author_ids": [FOSTER, MOREIRA]}, headers=h)
    assert r.status_code == 422 and r.json()["code"] == "conflicting_orcids"


def test_two_accounts_same_author_are_contested(client: TestClient) -> None:
    a, b = _login(client, "ada@example.edu"), _login(client, "bob@example.edu")
    client.post("/api/v1/profile/claim", json={"author_ids": [MOREIRA]}, headers=a)
    client.post("/api/v1/profile/claim", json={"author_ids": [MOREIRA]}, headers=b)
    me_a = client.get("/api/v1/me", headers=a).json()["researcher"]
    assert me_a["verified_level"] == "contested"
    assert "bob" not in str(client.get("/api/v1/me", headers=a).json())


def test_ensure_account_is_race_safe(client: TestClient) -> None:
    h = _login(client, "ada@example.edu")

    async def burst() -> None:
        c = build_container(_settings())
        sub = __import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "scout-dev:ada@example.edu")
        await asyncio.gather(*(c.identity.ensure_account(sub, "ada@example.edu") for _ in range(8)))
        await c.db.dispose()

    asyncio.run(burst())
    client.get("/api/v1/me", headers=h)
    from sqlalchemy import create_engine, text

    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM account")).scalar_one() == 1
        assert (
            conn.execute(text("SELECT count(*) FROM audit_log WHERE action='account.created'")).scalar_one()
            == 1
        )
    eng.dispose()
