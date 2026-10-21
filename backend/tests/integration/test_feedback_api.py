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


def _body(oid: str, **kw: Any) -> dict[str, Any]:
    return {"target_type": "opportunity", "target_id": oid, "rating": "up"} | kw


def test_a_thumb_alone_keeps_saved_reasons_and_comment_and_reasons_fit_the_target(
    finished: tuple[TestClient, dict[str, str], dict[str, Any]],  # noqa: F811
) -> None:
    """M4 review 3 and 6 through the API on PostgreSQL."""
    api, h, run = finished
    oid = api.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=h).json()["items"][0]["id"]
    saved = api.post(
        "/api/v1/feedback",
        json=_body(oid, rating="down", reasons=["not_feasible", "too_vague"], comment="needs a lab"),
        headers=h,
    )
    assert saved.status_code == 200
    thumb = api.post("/api/v1/feedback", json=_body(oid, rating="up"), headers=h).json()  # a card click
    assert thumb["rating"] == "up" and thumb["reasons"] == ["not_feasible", "too_vague"]
    assert thumb["comment"] == "needs a lab"
    cleared = api.post("/api/v1/feedback", json=_body(oid, reasons=[], comment=None), headers=h).json()
    assert cleared["reasons"] == [] and cleared["comment"] is None
    wrong = api.post("/api/v1/feedback", json=_body(oid, reasons=["methods_fit"]), headers=h)
    assert wrong.status_code == 422  # a match chip on an opportunity


def test_a_hidden_opportunity_cannot_be_rated(
    finished: tuple[TestClient, dict[str, str], dict[str, Any]],  # noqa: F811
) -> None:
    """M4 review 5: the check goes through the discovery service, so the evidence-shown rule applies."""
    from sqlalchemy import create_engine, text
    from tests.conftest import TEST_URL

    api, h, run = finished
    oid = api.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=h).json()["items"][0]["id"]
    assert api.post("/api/v1/feedback", json=_body(oid), headers=h).status_code == 200
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:  # e.g. a source document was deleted: evidence no longer verified
        conn.execute(
            text(
                "UPDATE evidence SET verified = false WHERE claim_id IN "
                "(SELECT id FROM claim WHERE opportunity_id = :o)"
            ),
            {"o": oid},
        )
    eng.dispose()
    assert api.get(f"/api/v1/opportunities/{oid}", headers=h).status_code == 404
    assert api.post("/api/v1/feedback", json=_body(oid, rating="down"), headers=h).status_code == 404
