"""Matches API: authentication, empty state, consent gate and not-found answers."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_discovery_fixtures import _login, client  # noqa: F401

pytestmark = pytest.mark.integration


def test_matches_endpoints_for_a_new_account(client: TestClient) -> None:  # noqa: F811
    assert client.get("/api/v1/matches").status_code == 401
    h = _login(client, "matcher@example.edu")
    empty = client.get("/api/v1/matches", headers=h)
    assert empty.status_code == 200 and empty.json() == {
        "matching_enabled": False,
        "items": [],
        "next_cursor": None,
    }
    mid = str(uuid4())
    assert client.get(f"/api/v1/matches/{mid}", headers=h).status_code == 404
    assert (
        client.post(f"/api/v1/matches/{mid}/decision", json={"action": "accept"}, headers=h).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/matches/{mid}/decision", json={"action": "boom"}, headers=h).status_code == 422
    )
    assert client.post(f"/api/v1/matches/{mid}/rating", json={"stars": 4}, headers=h).status_code == 404
    assert client.post(f"/api/v1/matches/{mid}/rating", json={"stars": 9}, headers=h).status_code == 422
    on = client.patch("/api/v1/me/consent", json={"consent_matching": True}, headers=h)
    assert on.status_code == 200
    assert client.get("/api/v1/matches", headers=h).json()["matching_enabled"] is True
    assert (
        client.post(f"/api/v1/matches/{mid}/decision", json={"action": "accept"}, headers=h).status_code
        == 404
    )
