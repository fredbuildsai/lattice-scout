"""Global budget guard on PostgreSQL: spend rows pause new runs, the status endpoint explains why (M6 slice B)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL
from tests.integration.test_discovery_fixtures import NOW, _login, client  # noqa: F401

pytestmark = pytest.mark.integration


def _spend(llm_usd: float) -> None:
    eng = create_engine(TEST_URL)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO llm_call (id, ts, feature, agent, route, provider, model, input_tokens, output_tokens,"
                " latency_ms, cost_usd, status, prompt_version) VALUES (gen_random_uuid(), :ts, 'discovery',"
                " 'GapAnalyst', 'smart', 'fake', 'fake-1', 1, 1, 1, :c, 'ok', 'v1')"
            ),
            {"ts": NOW, "c": llm_usd},
        )
    eng.dispose()


def test_status_is_open_until_the_llm_cap_is_spent(client: TestClient) -> None:  # noqa: F811
    h = _login(client, "budget-user@example.edu")
    assert client.get("/api/v1/system/status", headers=h).json() == {
        "accepting_runs": True,
        "message": None,
        "confidential_data_enabled": True,
        "confidential_data_notice": None,
    }


def test_a_spent_cap_pauses_new_runs_with_a_clear_message(db: object, tmp_path: object) -> None:
    from pathlib import Path

    from tests.integration.test_discovery_fixtures import _settings

    from scout.api.app import create_app
    from scout.bootstrap import build_container
    from scout.shared import FixedClock

    _spend(25)  # above the 20 USD default
    with TestClient(create_app(build_container(_settings(Path(str(tmp_path))), clock=FixedClock(NOW)))) as c:
        h = _login(c, "budget-user2@example.edu")
        st = c.get("/api/v1/system/status", headers=h).json()
        assert st["accepting_runs"] is False and "tomorrow" in st["message"]
        r = c.post("/api/v1/runs", json={"document_ids": []}, headers=h)
        assert r.status_code == 503 and r.json()["code"] == "service_paused"
