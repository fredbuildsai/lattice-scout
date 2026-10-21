"""M2 acceptance (plan M2): upload a 30-page declined proposal, get ``ready`` insights with verbatim reviewer
objections linked to chunk ids; discard policy removes the original; deleting removes storage object, chunks and
insights. Runs against PostgreSQL with the real worker handlers, local storage and the fixture LLM."""

from __future__ import annotations

import asyncio
import importlib.util
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL

from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
QUOTE = "The applicants do not demonstrate that the foams meet fire-safety standard EN 13501"


def _samples() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_sample_docs", ROOT / "scripts" / "make_sample_docs.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLES = _samples()
LONG_PDF = SAMPLES.to_pdf(SAMPLES.long_proposal())
PROPOSAL_PDF = SAMPLES.to_pdf(
    (SAMPLES.SOURCE / "declined-proposal-lignin-foams.md").read_text(encoding="utf-8")
)


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "storage", **kw
    )


@pytest.fixture
def make_client(db: object, tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(**kw: Any) -> TestClient:
        c = TestClient(create_app(build_container(_settings(tmp_path, **kw))))
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def _login(client: TestClient, email: str) -> dict[str, str]:
    r = client.post("/api/v1/dev/login", json={"email": email})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _worker(tmp_path: Path, **kw: Any) -> int:
    async def go() -> int:
        c = build_container(_settings(tmp_path, **kw))
        handlers = build_handlers(c)
        n = 0
        while await run_once(c.job_queue, handlers, "test-worker"):
            n += 1
        await c.db.dispose()
        return n

    return asyncio.run(go())


def _sql(q: str, **p: object) -> list[tuple[Any, ...]]:
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        rows = [tuple(r) for r in conn.execute(text(q), p)]
    engine.dispose()
    return rows


def _upload(client: TestClient, h: dict[str, str], data: bytes, name: str, **form: str) -> Any:
    return client.post(
        "/api/v1/documents", headers=h, files={"file": (name, data, "application/pdf")}, data=form
    )


def test_acceptance_30_page_proposal_discard_then_delete(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = make_client()
    h = _login(client, "ada@example.edu")
    started = time.monotonic()
    r = _upload(client, h, LONG_PDF, "sample-long-proposal-30p.pdf")  # keep_original unticked -> discard
    assert r.status_code == 201 and r.headers["location"].endswith(r.json()["id"])
    doc = r.json()
    assert (
        doc["status"] == "uploaded" and doc["retention"] == "discard" and doc["original_available"] is False
    )
    doc_id = doc["id"]

    assert _worker(tmp_path) >= 1
    assert time.monotonic() - started < 120  # plan: ready within 2 minutes
    detail = client.get(f"/api/v1/documents/{doc_id}", headers=h).json()
    assert detail["status"] == "ready" and detail["pages"] == 30 and detail["kind"] == "declined_grant"
    assert detail["kind_source"] == "scout" and detail["retention"] == "discard"
    assert detail["original_available"] is False
    ins = detail["insights"]
    assert len(ins["reviewer_objections"]) == 2 and ins["aims"]
    for obj in ins["reviewer_objections"]:
        assert obj["refs"] and all(ref["page_from"] >= 1 for ref in obj["refs"])
    assert any(QUOTE in o["quote"] for o in ins["reviewer_objections"])

    # discard: no original bytes, no chunk text, but quotes survive
    assert not list((tmp_path / "storage").rglob("*.*")) and not any(
        p.is_file() for p in (tmp_path / "storage").rglob("*")
    )
    assert _sql("SELECT count(*) FROM document_chunk WHERE text IS NOT NULL") == [(0,)]
    quotes = _sql("SELECT quotes FROM document_chunk WHERE quotes <> '[]'::jsonb")
    assert quotes and all(len(q["text"]) <= 300 for (qs,) in quotes for q in qs)

    # another user sees nothing
    other = _login(client, "bob@example.edu")
    assert client.get(f"/api/v1/documents/{doc_id}", headers=other).status_code == 404
    assert (
        client.patch(f"/api/v1/documents/{doc_id}", json={"kind": "other"}, headers=other).status_code == 404
    )
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=other).status_code == 404
    assert client.get("/api/v1/documents", headers=other).json()["items"] == []

    # the owner can reclassify
    patched = client.patch(f"/api/v1/documents/{doc_id}", json={"kind": "proposal"}, headers=h).json()
    assert patched["kind"] == "proposal" and patched["kind_source"] == "user"

    # delete: content gone at once, file and row gone after the job
    assert client.delete(f"/api/v1/documents/{doc_id}", headers=h).status_code == 204
    assert client.get(f"/api/v1/documents/{doc_id}", headers=h).status_code == 404
    assert _sql("SELECT count(*) FROM document_chunk") == [(0,)] and _sql(
        "SELECT count(*) FROM document_insights"
    ) == [(0,)]
    _worker(tmp_path)
    assert _sql("SELECT count(*) FROM document") == [(0,)]
    assert client.get("/api/v1/documents", headers=h).json()["items"] == []


def test_keep_original_and_delete_removes_the_object(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = make_client()
    h = _login(client, "ada@example.edu")
    r = _upload(client, h, PROPOSAL_PDF, "declined-proposal-lignin-foams.pdf", keep_original="true")
    doc = r.json()
    assert doc["retention"] == "keep"
    _worker(tmp_path)
    objects = [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]
    assert len(objects) == 1
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()
    assert detail["status"] == "ready" and detail["original_available"] is True
    assert len(detail["insights"]["reviewer_objections"]) == 3
    assert _sql("SELECT count(*) FROM document_chunk WHERE text IS NULL") == [(0,)]
    assert client.delete(f"/api/v1/documents/{doc['id']}", headers=h).status_code == 204
    _worker(tmp_path)
    assert not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]
    assert _sql("SELECT count(*) FROM document") == [(0,)]


def test_duplicates_validation_and_auth(make_client: Callable[..., TestClient], tmp_path: Path) -> None:
    client = make_client(vault_max_bytes=len(PROPOSAL_PDF) + 10)
    h = _login(client, "ada@example.edu")
    first = _upload(client, h, PROPOSAL_PDF, "a.pdf")
    again = _upload(client, h, PROPOSAL_PDF, "b.pdf")
    assert first.status_code == 201 and again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert len(client.get("/api/v1/documents", headers=h).json()["items"]) == 1

    bad = _upload(client, h, b"not a document" * 20, "x.pdf")
    assert bad.status_code == 415 and bad.json()["code"] == "unsupported_file_type"
    big = _upload(client, h, PROPOSAL_PDF + b"0" * 100, "big.pdf")
    assert big.status_code == 413 and big.json()["code"] == "file_too_large"
    assert client.get("/api/v1/documents").status_code == 401
    assert client.post("/api/v1/documents", files={"file": ("a.pdf", PROPOSAL_PDF)}).status_code == 401
    assert (
        client.patch(
            f"/api/v1/documents/{first.json()['id']}", json={"kind": "nonsense"}, headers=h
        ).status_code
        == 422
    )


def test_scanned_pdf_is_rejected_and_never_returned_as_insights(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    import io

    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for _ in range(2):
        c.rect(50, 50, 300, 500, fill=1)
        c.showPage()
    c.save()
    client = make_client()
    h = _login(client, "ada@example.edu")
    doc = _upload(client, h, buf.getvalue(), "scan.pdf").json()
    _worker(tmp_path)
    detail = client.get(f"/api/v1/documents/{doc['id']}", headers=h).json()
    assert (
        detail["status"] == "rejected"
        and detail["failure_code"] == "no_text_layer"
        and detail["insights"] is None
    )
    assert not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]  # discard policy: bytes gone


def test_delete_all_needs_the_word_and_clears_the_vault_once(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    """M6 review 9b: ``POST /documents/delete-all`` over PostgreSQL with the real handlers."""
    client = make_client()
    h = _login(client, "ada@example.edu")
    for name, pdf in (("one.pdf", PROPOSAL_PDF), ("two.pdf", LONG_PDF)):  # same bytes would be deduplicated
        assert _upload(client, h, pdf, name, keep_original="true").status_code == 201
    _worker(tmp_path)
    assert len([p for p in (tmp_path / "storage").rglob("*") if p.is_file()]) == 2
    assert _sql("SELECT count(*) FROM document_chunk")[0][0] > 0

    wrong = client.post("/api/v1/documents/delete-all", json={"confirm": "delete"}, headers=h)
    assert wrong.status_code == 422 and len(client.get("/api/v1/documents", headers=h).json()["items"]) == 2

    ok = client.post("/api/v1/documents/delete-all", json={"confirm": "DELETE"}, headers=h)
    assert ok.status_code == 202 and ok.json() == {"deleting": 2}
    assert client.get("/api/v1/documents", headers=h).json()["items"] == []
    assert _sql("SELECT count(*) FROM document_chunk") == [(0,)]
    assert _sql("SELECT count(*) FROM document_insights") == [(0,)]
    jobs = dict(_sql("SELECT kind, count(*) FROM job WHERE status = 'queued' GROUP BY kind"))
    assert jobs.get("vault.delete") == 2 and jobs.get("matching.profile", 0) >= 1

    again = client.post("/api/v1/documents/delete-all", json={"confirm": "DELETE"}, headers=h)
    assert again.json() == {"deleting": 0}
    _worker(tmp_path)
    assert _sql("SELECT count(*) FROM document") == [(0,)]
    assert not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]
