"""Notifications on PostgreSQL: preferences API, and the mail sent when a run finishes (M6 slice D)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL
from tests.integration.test_discovery_api import finished  # noqa: F401
from tests.integration.test_discovery_fixtures import _login, client  # noqa: F401

pytestmark = pytest.mark.integration


def test_preferences_default_and_partial_update(client: TestClient) -> None:  # noqa: F811
    h = _login(client, "notify-user@example.edu")
    assert client.get("/api/v1/me/notifications", headers=h).json() == {
        "email_runs": True, "email_matches": True, "email_digest": False,
    }  # fmt: skip
    r = client.patch("/api/v1/me/notifications", json={"email_digest": True, "email_runs": False}, headers=h)
    assert r.json() == {"email_runs": False, "email_matches": True, "email_digest": True}
    assert client.get("/api/v1/me/notifications", headers=h).json()["email_digest"] is True
    assert client.patch("/api/v1/me/notifications", json={"bogus": 1}, headers=h).status_code == 422


def test_a_finished_run_logs_exactly_one_mail(
    finished: tuple[TestClient, dict[str, str], dict[str, Any]],  # noqa: F811
) -> None:
    _, _, run = finished
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT template, ref FROM notification")).all()
    eng.dispose()
    assert [(r.template, r.ref) for r in rows] == [("run_finished", run["id"])]
