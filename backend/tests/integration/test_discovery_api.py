"""Discovery API behaviour on a finished fixture run: isolation, status changes, evidence, admin, cancel, limits."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_discovery_fixtures import CASES, _login, _worker, client  # noqa: F401

pytestmark = pytest.mark.integration


@pytest.fixture
def finished(
    client: TestClient,  # noqa: F811
    tmp_path: Path,
) -> tuple[TestClient, dict[str, str], dict]:
    case = CASES["moreira"]
    h = _login(client, "api-demo@example.edu")
    ids = case["author_ids"]
    assert (
        client.post(
            "/api/v1/profile/claim", json={"author_ids": ids, "primary_author_id": ids[0]}, headers=h
        ).status_code
        == 202
    )
    _worker(tmp_path)
    started = client.post("/api/v1/runs", json={"document_ids": []}, headers=h)
    assert started.status_code == 202 and started.headers["location"].endswith(started.json()["id"])
    _worker(tmp_path)
    run = client.get(f"/api/v1/runs/{started.json()['id']}", headers=h).json()
    assert run["status"] == "done"
    return client, h, run


def test_isolation_status_evidence_admin_and_limits(
    finished: tuple[TestClient, dict[str, str], dict],
) -> None:
    api, h, run = finished
    other = _login(api, "intruder@example.edu")
    cards = api.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=h).json()["items"]
    assert cards
    oid = cards[0]["id"]
    # another user sees nothing and learns nothing
    assert api.get(f"/api/v1/runs/{run['id']}", headers=other).status_code == 404
    assert api.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=other).status_code == 404
    assert api.get(f"/api/v1/opportunities/{oid}", headers=other).status_code == 404
    assert (
        api.patch(f"/api/v1/opportunities/{oid}", json={"status": "saved"}, headers=other).status_code == 404
    )
    assert api.post(f"/api/v1/runs/{run['id']}/cancel", headers=other).status_code == 404
    assert api.get("/api/v1/opportunities", headers=other).json()["items"] == []
    # save, dismiss (hidden from the default list), restore
    assert (
        api.patch(f"/api/v1/opportunities/{oid}", json={"status": "saved"}, headers=h).json()["status"]
        == "saved"
    )
    assert (
        api.patch(f"/api/v1/opportunities/{oid}", json={"status": "dismissed"}, headers=h).json()["status"]
        == "dismissed"
    )
    default = [c["id"] for c in api.get("/api/v1/opportunities", headers=h).json()["items"]]
    assert oid not in default
    assert (
        api.get("/api/v1/opportunities", params={"status": "dismissed"}, headers=h).json()["items"][0]["id"]
        == oid
    )
    assert api.patch(f"/api/v1/opportunities/{oid}", json={"status": "bogus"}, headers=h).status_code == 422
    # evidence opens for the owner only
    detail = api.get(f"/api/v1/opportunities/{oid}", headers=h).json()
    marker = next(m for c in detail["rationale"] for m in c["markers"] if m["source_type"] == "literature")
    opened = api.get(f"/api/v1/evidence/{marker['id']}/open", headers=h)
    assert opened.status_code == 200 and opened.json()["kind"] == "literature" and opened.json()["quote"]
    assert api.get(f"/api/v1/evidence/{marker['id']}/open", headers=other).status_code == 404
    # admin needs the role
    assert api.get("/api/v1/admin/claims", headers=h).status_code == 403
    # a terminal run cannot be cancelled; a second start is allowed once the first ended
    assert api.post(f"/api/v1/runs/{run['id']}/cancel", headers=h).status_code == 409
    assert (
        api.post(
            "/api/v1/runs", json={"document_ids": [str(__import__("uuid").uuid4())]}, headers=h
        ).status_code
        == 404
    )
    assert len(api.get("/api/v1/runs", headers=h).json()["items"]) == 1
