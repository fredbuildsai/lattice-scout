"""M2 vault, no vault text outside the vault tables (plan M2 test-first; design M2-vault-ingestion.md §10 invariant 9, §4 error mapping).

A canary string placed inside a document must never appear in audit rows, job payloads, `job.last_error`
(produced by `describe_exception` like the worker does), exception messages and their formatted tracebacks,
or captured log events; after `discard` it must not remain in the chunk rows or retained content either.
"""

from __future__ import annotations

import traceback
from typing import Any

import pytest
import structlog
from tests.unit.vault._env import CANARY, VaultEnv, drain, make_pages, pdf_bytes

from scout.shared import InfrastructureError, describe_exception
from scout.vault.domain.enums import DocStatus

DATA = pdf_bytes(make_pages(4, canary=CANARY))


def _assert_clean_outside_vault(env: VaultEnv, logs: list[Any]) -> None:
    for e in env.store.audit.entries:
        assert CANARY not in e.model_dump_json()
    for j in env.jobs.jobs.values():
        assert CANARY not in j.model_dump_json(), f"job {j.kind} leaks"
    assert CANARY not in repr(logs)
    for d in env.store.documents.values():
        assert CANARY not in repr(d)


def _assert_exception_clean(exc: BaseException) -> None:
    assert CANARY not in str(exc) and CANARY not in repr(exc) and CANARY not in describe_exception(exc)
    assert CANARY not in "".join(traceback.format_exception(exc))
    assert exc.__cause__ is None


def test_the_canary_is_really_in_the_document() -> None:
    assert CANARY in "".join(make_pages(4, canary=CANARY))


@pytest.mark.parametrize("keep", [True, False])
async def test_successful_ingestion_leaks_nothing(keep: bool) -> None:
    env = VaultEnv()
    with structlog.testing.capture_logs() as logs:
        doc = await env.upload(DATA, keep=keep)
        await env.ingest(doc)
    assert env.doc(doc.id).status is DocStatus.READY
    _assert_clean_outside_vault(env, logs)
    assert CANARY not in repr(env.store.insights)
    if not keep:
        assert CANARY not in repr([(r.text, r.retained) for r in env.store.chunks[doc.id]])
        assert not env.storage.objects
    else:
        assert any(CANARY in (r.text or "") for r in env.store.chunks[doc.id])  # the vault itself may hold it


def _echo_text(req: Any) -> dict[str, Any]:
    raise InfrastructureError("upstream rejected: " + " ".join(c.text for c in req.chunks))


def _invalid_with_text(req: Any) -> dict[str, Any]:
    return {"kind": "x " + " ".join(c.text for c in req.chunks)}


@pytest.mark.parametrize("script", [_echo_text, _invalid_with_text])
@pytest.mark.parametrize("keep", [True, False])
async def test_llm_failures_that_echo_the_text_do_not_leak(script: Any, keep: bool) -> None:
    env = VaultEnv()
    env.llm.script("InsightExtractor", script)
    with structlog.testing.capture_logs() as logs:
        doc = await env.upload(DATA, keep=keep)
        with pytest.raises(Exception) as info:
            await env.ingest(doc, last=False)
        _assert_exception_clean(info.value)
        with pytest.raises(Exception) as last:
            await env.ingest(doc, last=True)
        _assert_exception_clean(last.value)
    d = env.doc(doc.id)
    assert d.status is DocStatus.FAILED and d.failure_code == "insights_unavailable"
    _assert_clean_outside_vault(env, logs)
    if not keep:
        assert CANARY not in repr([(r.text, r.retained) for r in env.store.chunks[doc.id]])


@pytest.mark.parametrize("script", [_echo_text, _invalid_with_text])
async def test_job_last_error_written_by_the_worker_path_does_not_leak(script: Any) -> None:
    env = VaultEnv()
    env.llm.script("InsightExtractor", script)
    await env.upload(DATA, keep=False)  # enqueues the real vault.ingest job
    with structlog.testing.capture_logs() as logs:
        runs = await drain(env, {"vault.ingest": env.ingest_uc().handle})
    assert runs >= 2  # retried, then dead
    errors = [j.last_error for j in env.jobs.jobs.values() if j.kind == "vault.ingest"]
    assert errors and all(e and CANARY not in e for e in errors)
    _assert_clean_outside_vault(env, logs)
