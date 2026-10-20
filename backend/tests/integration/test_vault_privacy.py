"""M2 review round 1 (#1, #3, #5): privacy and concurrency checks against PostgreSQL."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from structlog.testing import capture_logs
from tests.conftest import TEST_URL

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.ports.llm import CallContext
from scout.settings import Settings
from scout.shared import AccountId, describe_exception
from scout.vault.adapters.extraction import PdfDocxExtractor
from scout.vault.adapters.sql import SqlVaultUnitOfWork
from scout.vault.application.extract_insights import ExtractInsights
from scout.vault.application.ingest import IngestDocument
from scout.vault.domain.enums import DocStatus, DocumentMime
from scout.vault.domain.insights import DocumentInsights, Item, Stage
from scout.vault.ports.insights import InsightOutcome, InsightRequest
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration
CANARY = "CANARY-private-text-91c2"
ROOT = Path(__file__).resolve().parents[3]


def _samples() -> Any:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLES = _samples()
PDF = SAMPLES.to_pdf(f"# Private note\n\nThe {CANARY} aim is to study lignin foams in depth. " * 12)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "s", **kw
    )


@pytest.fixture
def client(db: object, tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(build_container(_settings(tmp_path)))) as c:
        yield c


def _login(client: TestClient, email: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {client.post('/api/v1/dev/login', json={'email': email}).json()['access_token']}"
    }


def _worker(tmp_path: Path, script: Callable[[Any], Any] | None = None) -> None:
    async def go() -> None:
        c = build_container(_settings(tmp_path))
        if script is not None:
            c.llm.script("InsightExtractor", script)  # type: ignore[attr-defined]
        handlers = build_handlers(c)
        while await run_once(c.job_queue, handlers, "w"):
            pass
        await c.db.dispose()

    asyncio.run(go())


def _sql(q: str) -> list[tuple[Any, ...]]:
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        rows = [tuple(r) for r in conn.execute(text(q))]
    engine.dispose()
    return rows


def test_unverified_items_are_stored_but_never_returned(client: TestClient, tmp_path: Path) -> None:
    h = _login(client, "ada@example.edu")
    r = client.post(
        "/api/v1/documents",
        headers=h,
        files={"file": ("note.pdf", PDF, "application/pdf")},
        data={"keep_original": "true"},
    )
    assert r.status_code == 201

    def scripted(req: Any) -> dict[str, Any]:
        label = req.chunks[0].chunk_id
        return {
            "kind": "other",
            "aims": [
                {"summary": "Study lignin foams", "chunk_ids": [label]},
                {"summary": "UNCITED-CLAIM", "chunk_ids": []},
            ],
            "findings": [{"summary": "mail jane@example.edu", "chunk_ids": [label]}],
            "reviewer_objections": [
                {
                    "quote": "FABRICATED quote that the document never contains",
                    "summary": "x",
                    "chunk_ids": [label],
                }
            ],
            "title": "contact jane@example.edu",
            "keywords": ["lignin", "bob@example.edu"],
        }

    _worker(tmp_path, scripted)
    body = client.get(f"/api/v1/documents/{r.json()['id']}", headers=h).text
    for secret in ("UNCITED-CLAIM", "FABRICATED", "jane@example.edu", "bob@example.edu"):
        assert secret not in body
    assert "Study lignin foams" in body and "lignin" in body
    stored = json.dumps(_sql("SELECT payload FROM document_insights")[0][0])
    assert (
        "UNCITED-CLAIM" in stored and "FABRICATED" in stored
    )  # unverified claims are persisted, never deleted


def test_nul_in_model_output_does_not_fail_the_job_or_leak(client: TestClient, tmp_path: Path) -> None:
    h = _login(client, "ada@example.edu")
    doc = client.post(
        "/api/v1/documents", headers=h, files={"file": ("note.pdf", PDF, "application/pdf")}
    ).json()

    def with_nul(req: Any) -> dict[str, Any]:
        return {
            "kind": "other",
            "aims": [{"summary": f"{CANARY} aim\x00 with a NUL", "chunk_ids": [req.chunks[0].chunk_id]}],
        }

    with capture_logs() as logs:
        _worker(tmp_path, with_nul)
    assert client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()["status"] == "ready"
    assert _sql("SELECT count(*) FROM job WHERE last_error IS NOT NULL") == [(0,)]
    assert CANARY not in json.dumps(logs, default=str)


async def test_database_error_descriptions_never_quote_row_or_json_text(db: Any) -> None:
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError) as caught:
        async with db.engine.begin() as conn:
            await conn.execute(text("SELECT CAST(:v AS jsonb)"), {"v": '{"a": "' + CANARY + '\\u0000"}'})
    assert CANARY in str(caught.value)  # premise: the driver message does quote the text
    safe = describe_exception(caught.value, limit=100_000)
    assert CANARY not in safe and "sqlstate=" in safe


# ---- concurrency on real locks -------------------------------------------------------------------------------


class _Blocking:
    """Insight extractor that waits until released, so the test can act while analysis is in flight."""

    def __init__(self) -> None:
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def extract(self, req: InsightRequest, ctx: CallContext) -> InsightOutcome:
        self.entered.set()
        await self.release.wait()
        return InsightOutcome(
            DocumentInsights(aims=[Item(summary="s", chunk_ids=["c0"])]), "x@1", "fake", 0.0
        )


async def test_delete_while_analysis_runs_leaves_no_insights_and_a_skipped_job(
    db: Any, tmp_path: Path
) -> None:
    c = build_container(_settings(tmp_path))
    await db.engine.dispose()
    owner = AccountId(c.ids.new())
    async with c.db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                " created_at, updated_at) VALUES (:id, 'o@example.edu', false, false, 'f', 'user', now(), now())"
            ),
            {"id": owner},
        )
    up = await c.upload_document.upload(owner, "note.pdf", PDF, True, None)
    blocking = _Blocking()
    ingest = IngestDocument(
        lambda: SqlVaultUnitOfWork(c.db), c.storage, PdfDocxExtractor(), ExtractInsights(blocking), c.clock
    )
    payload: dict[str, Any] = {
        "account_id": str(owner),
        "feature": "vault.ingest",
        "document_id": str(up.document.id),
    }
    task = asyncio.create_task(ingest.handle(payload, last_attempt=False))
    await asyncio.wait_for(blocking.entered.wait(), 30)
    await c.vault.delete(owner, up.document.id)  # the owner deletes while the LLM call is in flight
    blocking.release.set()
    result = await asyncio.wait_for(task, 30)
    assert result == {"skipped": "gone"}
    async with c.db.engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM document_insights"))).scalar() == 0
        assert (await conn.execute(text("SELECT count(*) FROM document_chunk"))).scalar() == 0
        status = (await conn.execute(text("SELECT status FROM document"))).scalar()
    assert status == DocStatus.DELETING.value
    await c.db.dispose()


def test_upload_guard_refuses_before_reading_the_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.requests import Request

    reads: list[int] = []
    original = Request.form

    def spy(self: Request, *a: Any, **kw: Any) -> Any:
        reads.append(1)
        return original(self, *a, **kw)

    monkeypatch.setattr(Request, "form", spy)
    big = {"file": ("big.pdf", b"%PDF-1.7 " + b"0" * (27 * 1024 * 1024), "application/pdf")}
    assert client.post("/api/v1/documents", files=big).status_code == 401  # no token
    assert (
        client.post("/api/v1/documents", files=big, headers={"Authorization": "Bearer forged"}).status_code
        == 401
    )
    h = _login(client, "ada@example.edu")
    too_big = client.post("/api/v1/documents", files=big, headers=h)
    assert too_big.status_code == 413 and too_big.json()["code"] == "file_too_large"
    assert reads == []  # the body was never parsed
    assert (
        client.post(
            "/api/v1/documents", files={"file": ("a.pdf", PDF, "application/pdf")}, headers=h
        ).status_code
        == 201
    )
    assert reads  # a normal upload is parsed
    assert DocumentMime.PDF
    _ = Stage.FULL
