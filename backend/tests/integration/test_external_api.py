"""External suggestions API on PostgreSQL with the recorded OpenAlex fixtures: auth, profile gate, validation, refresh,
state changes, not-found and the daily limit."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL
from tests.integration.test_discovery_fixtures import NOW, _login, _settings, _worker

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.shared import FixedClock

pytestmark = pytest.mark.integration
FOSTER = "A5059588273"


def _write(q: str, **p: object) -> None:
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text(q), p)
    engine.dispose()


@pytest.fixture
def client(db: object, tmp_path: Path) -> Iterator[TestClient]:
    settings = _settings(tmp_path).model_copy(update={"external_refresh_per_day": 2})
    with TestClient(create_app(build_container(settings, clock=FixedClock(NOW)))) as c:
        yield c


def test_external_endpoints(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/api/v1/matches/external").status_code == 401
    h = _login(client, "external@example.edu")
    assert client.get("/api/v1/matches/external", headers=h).json() == {"items": []}
    no_profile = client.post("/api/v1/matches/external/refresh", json={"scopes": ["in_network"]}, headers=h)
    assert no_profile.status_code == 409 and no_profile.json()["code"] == "profile_required"
    bad = client.post("/api/v1/matches/external/refresh", json={"scopes": ["out_of_field"]}, headers=h)
    assert bad.status_code == 422
    assert client.post("/api/v1/matches/external/refresh", json={"scopes": []}, headers=h).status_code == 422
    unknown = client.patch(f"/api/v1/matches/external/{uuid4()}", json={"state": "saved"}, headers=h)
    assert unknown.status_code == 404
    assert client.get("/api/v1/matches/external?layer=boom", headers=h).status_code == 422

    claim = client.post(
        "/api/v1/profile/claim", json={"author_ids": [FOSTER], "primary_author_id": FOSTER}, headers=h
    )
    assert claim.status_code == 202
    _worker(tmp_path)
    first = client.post("/api/v1/matches/external/refresh", json={}, headers=h)
    assert first.status_code == 200, first.json()
    items = first.json()["items"]
    for it in items:  # the recordings hold few authors: whatever is found must be a well-formed outsider
        assert (
            it["openalex_url"] == f"https://openalex.org/{it['openalex_id']}" and it["openalex_id"] != FOSTER
        )
        assert it["layer"] in {"in_network", "in_field"} and it["formula"] == "ext1" and it["score"] >= 0.11
    # The recordings hold only the demo members, so seed one outsider row to exercise the triage path.
    sid = str(uuid4())
    _write(
        "INSERT INTO match_external_suggestion (id, account_id, openalex_id, name, score, topic_overlap, layer, vias)"
        " SELECT :id, account_id, 'A1', 'Ada Outsider', 0.5, 0.5, 'in_network', '{Bea}' FROM researcher"
        " WHERE :oa = ANY(SELECT openalex_author_id FROM researcher_openalex_id x WHERE x.researcher_id = researcher.id)",
        id=sid, oa=FOSTER,
    )  # fmt: skip
    listed = client.get("/api/v1/matches/external?layer=in_network", headers=h).json()["items"]
    assert [(x["name"], x["vias"], x["openalex_url"]) for x in listed] == [
        ("Ada Outsider", ["Bea"], "https://openalex.org/A1")
    ]
    saved = client.patch(f"/api/v1/matches/external/{sid}", json={"state": "saved"}, headers=h)
    assert saved.status_code == 200 and saved.json()["state"] == "saved"
    other = _login(client, "someone-else@example.edu")
    assert (
        client.patch(f"/api/v1/matches/external/{sid}", json={"state": "active"}, headers=other).status_code
        == 404
    )
    gone = client.patch(f"/api/v1/matches/external/{sid}", json={"state": "dismissed"}, headers=h)
    assert gone.json()["state"] == "dismissed"
    assert sid not in {x["id"] for x in client.get("/api/v1/matches/external", headers=h).json()["items"]}
    assert (
        client.patch(f"/api/v1/matches/external/{uuid4()}", json={"state": "nope"}, headers=h).status_code
        == 422
    )
    second = client.post("/api/v1/matches/external/refresh", json={"scopes": ["in_field"]}, headers=h)
    assert second.status_code == 200
    limited = client.post("/api/v1/matches/external/refresh", json={"scopes": ["in_field"]}, headers=h)
    assert limited.status_code == 429 and limited.json()["code"] == "external_refresh_limit"
