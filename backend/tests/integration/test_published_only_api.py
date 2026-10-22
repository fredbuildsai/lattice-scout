"""ADR 016 end to end on PostgreSQL: no authorized provider means documents wait, runs finish on published data."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import TEST_URL
from tests.integration.test_discovery_fixtures import CASES
from tests.integration.test_vault_api import PROPOSAL_PDF

from scout.api.app import create_app
from scout.api.schemas.discovery import PUBLISHED_ONLY_NOTE_NO_DOCUMENTS
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.shared import FixedClock
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _settings(tmp_path: Path, authorized: bool = False) -> Settings:
    return Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "storage",
        llm_fake_confidential_allowed=authorized,
    )  # fmt: skip


def _worker(tmp_path: Path, authorized: bool = False) -> None:
    async def go() -> None:
        c = build_container(_settings(tmp_path, authorized), clock=FixedClock(NOW))
        handlers = build_handlers(c)
        while await run_once(c.job_queue, handlers, "test-worker"):
            pass
        await c.db.dispose()

    asyncio.run(go())


@pytest.fixture
def client(db: object, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(build_container(_settings(tmp_path), clock=FixedClock(NOW)))) as c:
        yield c


def _login(client: TestClient, email: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {client.post('/api/v1/dev/login', json={'email': email}).json()['access_token']}"
    }


def test_documents_wait_and_the_run_finishes_on_published_data_with_the_note(
    client: TestClient, tmp_path: Path
) -> None:
    h = _login(client, "published-only@example.edu")
    ids = CASES["moreira"]["author_ids"]
    assert (
        client.post(
            "/api/v1/profile/claim", json={"author_ids": ids, "primary_author_id": ids[0]}, headers=h
        ).status_code
        == 202
    )
    status = client.get("/api/v1/system/status", headers=h).json()
    assert (
        status["confidential_data_enabled"] is False
        and "published work only" in status["confidential_data_notice"]
    )
    up = client.post(
        "/api/v1/documents",
        files={"file": ("declined-proposal-lignin-foams.pdf", PROPOSAL_PDF, "application/pdf")},
        data={"keep_original": "true"},
        headers=h,
    )
    assert up.status_code in (200, 201, 202), up.text
    _worker(tmp_path)
    doc = client.get(f"/api/v1/documents/{up.json()['id']}", headers=h).json()
    assert doc["status"] == "awaiting_approved_provider" and not doc["insights"]
    started = client.post("/api/v1/runs", json={"document_ids": []}, headers=h)
    assert started.status_code == 202
    _worker(tmp_path)
    run = client.get(f"/api/v1/runs/{started.json()['id']}", headers=h).json()
    assert (
        run["status"] == "done"
        and run["basis"] == "published_only"
        and run["basis_note"] == PUBLISHED_ONLY_NOTE_NO_DOCUMENTS  # started without documents
    )
    cards = client.get("/api/v1/opportunities", params={"run_id": run["id"]}, headers=h).json()["items"]
    assert cards and all(c["basis"] == "published_only" and c["basis_note"] for c in cards)
    # the owner authorizes a provider later: the parked document is analysed by the next sweep
    from scout.vault.application.sweep import SweepVault  # noqa: F401  (handler registered as vault.sweep)

    async def sweep() -> None:
        c = build_container(_settings(tmp_path, True), clock=FixedClock(NOW))
        await c.sweep_vault.handle({}, last_attempt=False)
        handlers = build_handlers(c)
        while await run_once(c.job_queue, handlers, "test-worker"):
            pass
        await c.db.dispose()

    asyncio.run(sweep())
    assert client.get(f"/api/v1/documents/{up.json()['id']}", headers=h).json()["status"] == "ready"
