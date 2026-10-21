"""The FakeLLM discovery fixtures drive a real run on PostgreSQL (plan M3 A1 on the demo persona).

For each eval case (``eval/cases/*.yaml``: Johan Foster with the three fictional sample documents, Raphaell Moreira
profile-only) it claims the public OpenAlex records, uploads the documents through the API, POSTs ``/api/v1/runs`` and
drains the worker with the real handlers, ``FixtureLiterature`` and the fixtures written by
``scripts/make_discovery_fixtures.py``. Every agent input must hit its hash-named fixture (no ``default.json``
fallback, no unexpected degraded step), and the run must show 3-10 opportunities, each with at least two distinct
verified evidence items, one of them literature, and a measured count. The clock is fixed at the date the fixtures
were generated (their keys depend on the year).
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.shared import FixedClock
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)  # same as scripts/make_discovery_fixtures.py
CASES = {
    p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "eval" / "cases").glob("*.yaml"))
}
# Moreira's profile-only run has no adjacent cluster and uses the documented fallback (the 3 largest clusters).
DEGRADED_OK: dict[str, set[str]] = {"foster": set(), "moreira": {"no_adjacent_clusters"}}
EXPECTED_VERSIONS = {
    "QueryPlanner": "QueryPlanner@1.1.0",
    "ClusterLabeler": "ClusterLabeler@1.1.0",
    "GapAnalyst": "GapAnalyst@1.1.0",
    "Critic": "Critic@1.1.0",
}


def _samples() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "storage"
    )


@pytest.fixture
def client(db: object, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(build_container(_settings(tmp_path), clock=FixedClock(NOW)))) as c:
        yield c


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _worker(tmp_path: Path) -> int:
    """Process every due job with a separate container (own event loop), on the same fixed clock."""

    async def go() -> int:
        c = build_container(_settings(tmp_path), clock=FixedClock(NOW))
        handlers = build_handlers(c)
        n = 0
        while await run_once(c.job_queue, handlers, "test-worker"):
            n += 1
        await c.db.dispose()
        return n

    return asyncio.run(go())


def _sql(q: str, **p: object) -> list[tuple[Any, ...]]:
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        rows = [tuple(r) for r in conn.execute(text(q), p)]
    engine.dispose()
    return rows


def _key(m: dict[str, Any]) -> tuple[Any, ...]:
    """What makes two citations the same source (the show rule counts distinct sources)."""
    return (m["source_type"], m["work_id"], m["document_id"], m["page_from"], m["query"])


@pytest.mark.parametrize("case_id", sorted(CASES))
def test_fixture_run_shows_grounded_opportunities(client: TestClient, tmp_path: Path, case_id: str) -> None:
    CASE = CASES[case_id]
    h = _login(client, f"{case_id}-demo@example.edu")
    ids = CASE["author_ids"]
    r = client.post("/api/v1/profile/claim", json={"author_ids": ids, "primary_author_id": ids[0]}, headers=h)
    assert r.status_code == 202
    assert _worker(tmp_path) == 1
    assert client.get("/api/v1/profile", headers=h).json()["import"]["status"] == "done"

    samples = _samples()
    doc_ids = []
    for d in CASE["documents"]:
        src = ROOT / d["path"]
        pdf = samples.to_pdf(src.read_text(encoding="utf-8"))
        keep = "true" if d["retention"] == "keep" else "false"
        up = client.post(
            "/api/v1/documents", headers=h, files={"file": (f"{src.stem}.pdf", pdf, "application/pdf")},
            data={"keep_original": keep},
        )  # fmt: skip
        assert up.status_code == 201 and up.json()["retention"] == d["retention"], up.json()
        doc_ids.append(up.json()["id"])
    assert _worker(tmp_path) == len(doc_ids)
    for doc_id in doc_ids:
        assert client.get(f"/api/v1/documents/{doc_id}", headers=h).json()["status"] == "ready"

    started = client.post("/api/v1/runs", json={"document_ids": doc_ids}, headers=h)
    assert started.status_code == 202, started.json()
    run_id = started.json()["id"]
    assert _worker(tmp_path) >= 1

    run = client.get(f"/api/v1/runs/{run_id}", headers=h).json()
    assert run["status"] == "done" and run["percent"] == 100, run
    degraded = {d["reason"] for d in run["degraded"]}
    assert degraded <= DEGRADED_OK[case_id], degraded  # e.g. label_fallback: a fixture was missed
    assert 0 < float(run["cost_usd"]) < float(run["budget_usd"])
    (versions,) = _sql("SELECT prompt_versions FROM analysis_run WHERE id = :r", r=run_id)[0]
    assert {k: versions.get(k) for k in EXPECTED_VERSIONS} == EXPECTED_VERSIONS

    listed = client.get("/api/v1/opportunities", params={"run_id": run_id, "limit": 50}, headers=h)
    cards = listed.json()["items"]
    titles = [c["title"] for c in cards]
    assert CASE["min_shown"] <= len(cards) <= CASE["max_shown"] and 3 <= len(cards) <= 10, titles
    assert run["counts"]["shown"] == len(cards)
    for card in cards:
        detail = client.get(f"/api/v1/opportunities/{card['id']}", headers=h).json()
        markers = [m for c in detail["rationale"] + detail["objection"] for m in c["markers"]]
        sources = {_key(m) for m in markers}
        assert len(sources) >= 2, (card["title"], sources)
        assert any(m["source_type"] == "literature" for m in markers), card["title"]
        counts = [m for c in detail["score_claims"] for m in c["markers"] if m["source_type"] == "count"]
        assert counts and counts[0]["count"] == card["novelty"]["count"] and card["novelty"]["count"] > 0
        for m in markers:
            if m["source_type"] == "literature":
                assert m["work_id"] and m["quote"] and 15 <= len(m["quote"]) <= 300

    # A3: every verified literature citation resolves to a work of this run's corpus
    assert _sql(
        "SELECT count(*) FROM evidence e JOIN claim c ON c.id = e.claim_id JOIN opportunity o ON o.id = c.opportunity_id"
        " LEFT JOIN corpus_work w ON w.id = e.corpus_work_id AND w.run_id = o.run_id"
        " WHERE o.run_id = :r AND e.verified AND e.source_type = 'literature' AND w.id IS NULL",
        r=run_id,
    ) == [(0,)]
    # the critic-rejected (already done) draft and, for Foster, a deliberately broken citation are persisted, never shown
    statuses = dict(
        _sql("SELECT status, count(*) FROM opportunity WHERE run_id = :r GROUP BY status", r=run_id)
    )
    assert statuses.get("rejected_by_critic", 0) >= 1 and statuses.get("shown") == len(cards)
    if case_id != "foster":
        return
    assert _sql(
        "SELECT count(*) FROM evidence e JOIN claim c ON c.id = e.claim_id JOIN opportunity o ON o.id = c.opportunity_id"
        " WHERE o.run_id = :r AND e.check_code = 'no_abstract'",
        r=run_id,
    ) == [(1,)]
    shown_text = " ".join(
        c["text"]
        for card in cards
        for part in ("rationale", "objection")
        for c in client.get(f"/api/v1/opportunities/{card['id']}", headers=h).json()[part]
    )
    assert "one pot" not in shown_text
