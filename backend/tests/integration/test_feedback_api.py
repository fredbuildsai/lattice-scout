"""Feedback API on a finished fixture run: ownership, replacement, admin view."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.integration.test_discovery_api import finished  # noqa: F401
from tests.integration.test_discovery_fixtures import _login, client  # noqa: F401

pytestmark = pytest.mark.integration


def test_rate_replace_and_summarise(
    finished: tuple[TestClient, dict[str, str], dict[str, Any]],  # noqa: F811
) -> None:
    api, h, run = finished
    other = _login(api, "feedback-intruder@example.edu")
    oid = api.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=h).json()["items"][0]["id"]
    body = {"target_type": "opportunity", "target_id": oid, "rating": "up", "reasons": ["spot_on"]}
    assert api.get("/api/v1/feedback", params=body | {"rating": None}, headers=h).status_code == 404
    assert api.post("/api/v1/feedback", json=body, headers=other).status_code == 404
    ok = api.post("/api/v1/feedback", json=body, headers=h)
    assert ok.status_code == 200 and ok.json()["reasons"] == ["spot_on"]
    again = api.post(
        "/api/v1/feedback",
        json=body | {"rating": "down", "reasons": ["too_vague"], "comment": "hm"},
        headers=h,
    )
    assert again.json()["rating"] == "down"
    mine = api.get(
        "/api/v1/feedback", params={"target_type": "opportunity", "target_id": oid}, headers=h
    ).json()
    assert mine["rating"] == "down" and mine["comment"] == "hm"
    assert api.post("/api/v1/feedback", json=body | {"reasons": ["bogus"]}, headers=h).status_code == 422
    assert api.post("/api/v1/feedback", json=body | {"target_type": "match"}, headers=h).status_code == 422
    stars = {"target_type": "match", "target_id": oid, "stars": 4}
    assert api.post("/api/v1/feedback", json=stars, headers=h).status_code == 404  # not a mutual match
