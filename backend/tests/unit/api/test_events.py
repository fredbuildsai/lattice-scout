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
    """A BufferedWriter whose sink sleeps 1 s must not delay the 202."""
    import asyncio

    from scout.adapters.observability.buffer import BufferedWriter
    from scout.ports.analytics import AnalyticsEvent, SessionTouch

    class SlowAnalytics:
        def __init__(self) -> None:
            async def sink(batch: list[AnalyticsEvent]) -> None:
                await asyncio.sleep(1)

            self.w = BufferedWriter[AnalyticsEvent](
                sink, name="slow", capacity=1000, flush_ms=10, batch_size=5
            )

        def track(self, evt: AnalyticsEvent) -> None:
            self.w.offer(evt)

        def touch_session(self, s: SessionTouch) -> None:
            pass

    slow = SlowAnalytics()
    c = replace(build_container(Settings(_env_file=None, app_env="test")), analytics=slow)
    with TestClient(create_app(c)) as client:
        client.post("/api/v1/events", json=_batch(20))  # first batch may start the slow flush
        t0 = time.perf_counter()
        r = client.post("/api/v1/events", json=_batch(20))
        assert r.status_code == 202 and time.perf_counter() - t0 < 0.05


def test_email_and_free_text_pages_are_rejected(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    client, rec = env
    b = _batch(3)
    b["events"][0]["page"] = "/profile?name=jane"  # type: ignore[index]
    b["events"][1]["name"] = "menu_item_clicked"  # type: ignore[index]
    b["events"][1]["properties"] = {"item": "jane@example.edu"}  # type: ignore[index]
    r = client.post("/api/v1/events", json=b)
    assert r.json()["accepted"] == 1 and {x["index"] for x in r.json()["rejected"]} == {0, 1}
    assert len(rec.events) == 1


def test_oversize_body_is_rejected(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    r = env[0].post("/api/v1/events", content=b"x" * 70_000, headers={"Content-Type": "text/plain"})
    assert r.status_code == 422


def test_untrusted_headers_are_sanitised(env: tuple[TestClient, InMemoryAnalytics]) -> None:
    r = env[0].get("/health", headers={"X-Request-Id": "bad id\twith spaces"})
    assert r.headers["x-request-id"] != "bad id\twith spaces" and len(r.headers["x-request-id"]) == 32
