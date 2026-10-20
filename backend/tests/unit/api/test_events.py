import time
from collections.abc import Iterator
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from tests.fakes import InMemoryAnalytics

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings


@pytest.fixture
def env() -> Iterator[tuple[TestClient, InMemoryAnalytics]]:
    rec = InMemoryAnalytics()
    c = replace(build_container(Settings(_env_file=None, app_env="test")), analytics=rec)
    with TestClient(create_app(c)) as client:
        yield client, rec


def _batch(n: int = 1, name: str = "page_view") -> dict[str, object]:
    return {
        "session": {"id": str(uuid4()), "viewport": "1x1"},
        "events": [
            {"name": name, "page": "/home", "prev_page": "/x", "dwell_ms": 5, "seq": i} for i in range(n)
        ],
    }


def test_accepts_with_202_and_enriches(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    client, rec = env
    r = client.post(
        "/api/v1/events", json=_batch(3), headers={"User-Agent": "Mozilla/5.0 (iPhone) Mobile Safari/604"}
    )
    assert r.status_code == 202 and r.json() == {"accepted": 3, "rejected": []}
    assert len(rec.events) == 3 and rec.sessions[0].device_type == "mobile"


def test_unknown_event_rejected_per_index(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    client, _rec = env
    b = _batch(2)
    b["events"][1]["name"] = "nope"  # type: ignore[index]
    r = client.post("/api/v1/events", json=b)
    assert r.status_code == 202 and r.json()["accepted"] == 1 and r.json()["rejected"][0]["index"] == 1


def test_101_events_is_422(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    assert env[0].post("/api/v1/events", json=_batch(101)).status_code == 422


def test_text_plain_beacon(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    import json

    r = env[0].post("/api/v1/events", content=json.dumps(_batch()), headers={"Content-Type": "text/plain"})
    assert r.status_code == 202


def test_disabled_analytics_still_202() -> None:
    c = build_container(Settings(_env_file=None, app_env="test", analytics_enabled=False))
    with TestClient(create_app(c)) as client:
        assert client.post("/api/v1/events", json=_batch()).status_code == 202


def test_slow_sink_does_not_slow_response() -> None:
    class Slow:
        def track(self, evt: object) -> None:
            pass

        def touch_session(self, s: object) -> None:
            pass

    c = replace(build_container(Settings(_env_file=None, app_env="test")), analytics=Slow())
    with TestClient(create_app(c)) as client:
        t0 = time.perf_counter()
        client.post("/api/v1/events", json=_batch(20))
        assert time.perf_counter() - t0 < 0.2
