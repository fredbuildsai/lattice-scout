"""M2 vault, ExtractInsights (plan M2 test-first; design M2-vault-ingestion.md §5 "ExtractInsights", §10 invariant 3).

Real ``AgentsInsightExtractor`` over ``FakeLLM`` (scripted), recorded. Covers: single ``full`` call, map-reduce
(one map call per slice, bounded concurrency, reduce sees verified partials only, unverified map items persisted
with stage=map, final output verified against all chunks, summed cost), and error propagation.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest
from tests.unit.vault._env import (
    COST_PER_CALL,
    FAKE_QUOTE,
    OWNER,
    QUOTE,
    T0,
    UNVERIFIED_MARK,
    VaultEnv,
    make_pages,
    scripted_insights,
)

from scout.ports.llm import CallContext, LLMOutputInvalid, LLMUnavailable
from scout.shared import InfrastructureError
from scout.vault.domain.chunking import Chunk, chunk_pages
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocumentMime, Retention
from scout.vault.domain.insights import ItemStatus, Section, Stage, UnverifiedReason
from scout.vault.domain.slicing import plan_slices

DOC_ID = UUID(int=77)
CTX = CallContext(feature="vault.insights", account_id=OWNER)


def _doc(kind: DocKind | None = None) -> Document:
    return Document.upload(
        id_=DOC_ID, owner_id=OWNER, filename="proposal.pdf", mime=DocumentMime.PDF, size_bytes=1,
        sha256="0" * 64, storage_key=f"vault/{OWNER}/{DOC_ID}", retention=Retention.KEEP, kind_hint=kind, now=T0,
    )  # fmt: skip


def _chunks(n_pages: int) -> list[Chunk]:
    return chunk_pages(DOC_ID, make_pages(n_pages))


def _env_long(**kw: Any) -> tuple[VaultEnv, list[Chunk], int]:
    env = VaultEnv(full_max=8_000, slice_chars=5_000, **kw)
    chunks = _chunks(8)
    n = len(plan_slices(chunks, 8_000, 5_000))
    assert n >= 4  # the fixture really is a long document
    return env, chunks, n


async def test_short_document_is_one_full_call() -> None:
    env = VaultEnv()
    chunks = _chunks(3)
    res = await env.insights_uc().run(_doc(DocKind.PROPOSAL), chunks, CTX)
    (req,) = env.rec.requests
    assert req.mode == "full" and req.partials == () and req.filename == "proposal.pdf"
    assert req.kind_hint is DocKind.PROPOSAL
    assert [c.chunk_id for c in req.chunks] == [c.label for c in chunks]
    assert res.mode == "full" and res.slices == 1 and res.document_id == DOC_ID
    assert res.cost_usd == pytest.approx(COST_PER_CALL)
    assert res.prompt_version.startswith("InsightExtractor@") and res.model == "fake"
    assert res.kind_suggested is DocKind.DECLINED_GRANT and res.kind_reason == "Reviewer comments present"
    assert res.title == "Sample proposal" and res.keywords == ["lignin", "foam"]


async def test_full_call_items_are_verified_against_the_chunks_and_unverified_are_kept() -> None:
    env = VaultEnv()
    chunks = _chunks(3)
    res = await env.insights_uc().run(_doc(), chunks, CTX)
    assert {i.stage for i in res.items} == {Stage.FULL}
    by_summary = {i.data.get("quote") or i.data.get("summary"): i for i in res.items}
    aim = by_summary["Aim from c0"]
    assert (
        aim.status is ItemStatus.VERIFIED and aim.chunk_ids == (chunks[0].id,) and aim.chunk_labels == ("c0",)
    )
    objection = by_summary[QUOTE]
    assert objection.status is ItemStatus.VERIFIED and objection.section is Section.REVIEWER_OBJECTIONS
    assert set(objection.chunk_ids) <= {c.id for c in chunks}
    assert by_summary[UNVERIFIED_MARK].reason is UnverifiedReason.UNKNOWN_CHUNK
    assert by_summary[FAKE_QUOTE].reason is UnverifiedReason.QUOTE_NOT_FOUND
    # unverified items are persisted in ``items`` but never in ``visible()``
    assert len(res.items) == 4 and len(res.visible()) == 2
    assert all(i.status is ItemStatus.VERIFIED for i in res.visible())


async def test_long_document_runs_one_map_call_per_slice_then_one_reduce() -> None:
    env, chunks, n = _env_long()
    res = await env.insights_uc().run(_doc(), chunks, CTX)
    assert sorted(env.rec.modes()) == ["map"] * n + ["reduce"] and env.rec.modes()[-1] == "reduce"
    expected = sorted([c.label for c in s] for s in plan_slices(chunks, 8_000, 5_000))
    sent = sorted([c.chunk_id for c in r.chunks] for r in env.rec.requests if r.mode == "map")
    assert sent == expected
    assert res.mode == "map_reduce" and res.slices == n
    assert res.cost_usd == pytest.approx(COST_PER_CALL * (n + 1))


async def test_reduce_receives_only_verified_partials_and_no_chunks() -> None:
    env, chunks, n = _env_long()
    await env.insights_uc().run(_doc(), chunks, CTX)
    reduce_req = env.rec.requests[-1]
    assert reduce_req.mode == "reduce" and reduce_req.chunks == ()
    assert len(reduce_req.partials) == n
    sent = json.dumps([p.model_dump(mode="json") for p in reduce_req.partials])
    assert UNVERIFIED_MARK not in sent and FAKE_QUOTE not in sent and "c999" not in sent
    assert "Aim from c0" in sent and QUOTE in sent  # verified material did make it


async def test_unverified_map_items_are_persisted_with_stage_map_and_never_visible() -> None:
    env, chunks, n = _env_long()
    res = await env.insights_uc().run(_doc(), chunks, CTX)
    map_items = [i for i in res.items if i.stage is Stage.MAP]
    assert len(map_items) == 2 * n  # per slice: the unknown-chunk aim and the invented quote
    assert all(i.status is ItemStatus.UNVERIFIED and i.reason is not None for i in map_items)
    assert {i.reason for i in map_items} == {UnverifiedReason.UNKNOWN_CHUNK, UnverifiedReason.QUOTE_NOT_FOUND}
    assert not [i for i in res.visible() if i.stage is Stage.MAP]
    assert all(i.stage is Stage.REDUCE for i in res.visible())
    assert res.visible()  # the merge produced verified items


async def test_reduce_output_is_verified_against_all_chunks_not_just_one_slice() -> None:
    env, chunks, _ = _env_long()
    res = await env.insights_uc().run(_doc(), chunks, CTX)
    cross = next(i for i in res.items if i.data.get("summary") == "Cross-slice aim")
    assert cross.stage is Stage.REDUCE and cross.status is ItemStatus.VERIFIED
    slices = plan_slices(chunks, 8_000, 5_000)
    assert cross.chunk_ids == (slices[0][0].id, slices[-1][0].id)
    uncited = next(i for i in res.items if i.data.get("summary") == "Reduce finding without citation")
    assert uncited.stage is Stage.REDUCE and uncited.reason is UnverifiedReason.NO_CHUNK_REF
    assert uncited not in res.visible()
    obj = next(i for i in res.visible() if i.section is Section.REVIEWER_OBJECTIONS)
    assert obj.data["quote"] == QUOTE and obj.chunk_ids == (chunks[0].id,)
    assert res.kind_suggested is DocKind.DECLINED_GRANT and res.title == "Merged title"


@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_map_calls_never_exceed_the_concurrency_limit(limit: int) -> None:
    env, chunks, n = _env_long(concurrency=limit, delay=0.01)
    await env.insights_uc().run(_doc(), chunks, CTX)
    assert env.rec.max_in_flight == min(limit, n)


async def test_llm_unavailable_in_a_map_call_propagates() -> None:
    env, chunks, _ = _env_long()

    def boom(req: Any) -> dict[str, Any]:
        raise InfrastructureError("provider down: " + req.chunks[0].text[:40])

    env.llm.script("InsightExtractor", boom)
    with pytest.raises(LLMUnavailable):
        await env.insights_uc().run(_doc(), chunks, CTX)


async def test_failure_in_the_reduce_call_propagates() -> None:
    env, chunks, n = _env_long()

    def flaky(req: Any) -> dict[str, Any]:
        if req.mode == "reduce":
            raise InfrastructureError("reduce down")
        return scripted_insights(req)

    env.llm.script("InsightExtractor", flaky)
    with pytest.raises(LLMUnavailable):
        await env.insights_uc().run(_doc(), chunks, CTX)
    assert env.rec.modes().count("map") == n


async def test_invalid_model_output_raises_llm_output_invalid() -> None:
    env = VaultEnv()
    env.llm.script("InsightExtractor", {"kind": "not-a-kind"})
    with pytest.raises(LLMOutputInvalid):
        await env.insights_uc().run(_doc(), _chunks(2), CTX)


async def test_same_input_gives_the_same_result() -> None:
    chunks = _chunks(3)
    a = await VaultEnv().insights_uc().run(_doc(), chunks, CTX)
    b = await VaultEnv().insights_uc().run(_doc(), chunks, CTX)
    assert a == b
