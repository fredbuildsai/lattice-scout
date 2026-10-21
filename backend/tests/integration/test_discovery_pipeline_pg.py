"""The whole run pipeline on PostgreSQL (migration 0005): rows, idempotent re-runs and ledger-owned columns."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from tests.unit.discovery._world import DOC_ID, NOW, OWNER, World

from scout.adapters.persistence.db import Database
from scout.discovery.adapters.sql import SqlDiscoveryUnitOfWork, SqlRunLedger
from scout.discovery.domain.enums import OppStatus, RunStatus
from scout.vault.adapters.sql import SqlVaultUnitOfWork
from scout.vault.domain.chunking import ChunkParams, chunk_pages
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocumentMime, Retention

pytestmark = pytest.mark.integration


CHUNK = chunk_pages(
    DOC_ID,
    ["We aim to develop lignin foams for insulation panels. " * 3],
    ChunkParams(target=300, max=400, min=20),
)[0]


class PgWorld(World):
    db: Database

    def __init__(self, db: Database) -> None:
        super().__init__()
        self.db = db
        self.ledger = SqlRunLedger(db, self.clock)  # type: ignore[assignment]
        owner, mat, ready = self.vault.docs[DOC_ID]
        passage = replace(mat.passages[0], chunk_id=CHUNK.id)
        self.vault.add(owner, replace(mat, passages=(passage,)), ready)

    def uow(self) -> SqlDiscoveryUnitOfWork:  # type: ignore[override]
        return SqlDiscoveryUnitOfWork(self.db)


async def _account(db: Database) -> None:
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                " created_at, updated_at) VALUES (:id, 'o@example.edu', false, false, 'f', 'user', now(), now())"
            ),
            {"id": OWNER},
        )
    async with SqlVaultUnitOfWork(db) as vault:
        await vault.documents.add(
            Document.upload(
                id_=DOC_ID,
                owner_id=OWNER,
                filename="note.pdf",
                mime=DocumentMime.PDF,
                size_bytes=10,
                sha256="a" * 64,
                storage_key="vault/x",
                retention=Retention.KEEP,
                kind_hint=None,
                now=NOW,
            )  # fmt: skip
        )
        await vault.chunks.replace_all(DOC_ID, [CHUNK])
        await vault.commit()


async def _count(db: Database, table: str) -> int:
    async with db.engine.connect() as conn:
        return int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar() or 0)  # noqa: S608


async def test_pipeline_on_postgres_runs_to_done_and_a_rerun_adds_no_rows(db: Database) -> None:
    await _account(db)
    w = PgWorld(db)
    run = await w.start()
    first: dict[str, Any] = await w.run(run)
    assert first["status"] == "done", first
    async with w.uow() as uow:
        final = await uow.runs.get(run.id, OWNER)
        opps = await uow.opportunities.list_for_run(run.id)
    assert final is not None and final.status is RunStatus.DONE and final.percent() == 100
    assert final.spend.llm_calls == 8 and final.spend.openalex_calls == 3 and final.spend.cost_usd > 0
    assert [o.status for o in opps].count(OppStatus.SHOWN) == 1
    shown = next(o for o in opps if o.status is OppStatus.SHOWN)
    assert shown.scores is not None and len(shown.visible_claims()) == 3
    rows = {
        t: await _count(db, t)
        for t in ("corpus_work", "cluster", "novelty_probe", "opportunity", "claim", "evidence")
    }
    assert all(n > 0 for n in rows.values()), rows
    assert await w.run(run) == {"skipped": "done"}
    assert {t: await _count(db, t) for t in rows} == rows


async def test_saving_the_run_never_loses_ledger_spend(db: Database) -> None:
    await _account(db)
    w = PgWorld(db)
    run = await w.start()
    async with w.uow() as uow:  # a stale copy of the run is loaded ...
        stale = await uow.runs.get_for_update(run.id)
        assert stale is not None
        gen = stale.begin_execution(NOW)
        await uow.runs.save(stale)
        await uow.commit()
    await w.ledger.charge(run.id, gen, cost_usd=__import__("decimal").Decimal("0.5"), llm_calls=2)
    async with w.uow() as uow:  # ... and saved after the ledger charged: the spend must survive
        again = await uow.runs.get_for_update(run.id)
        assert again is not None and again.spend.llm_calls == 2
        again.spend = again.spend.__class__()  # a stale in-memory value
        await uow.runs.save(again)
        await uow.commit()
    async with w.uow() as uow:
        back = await uow.runs.get(run.id, OWNER)
    assert (
        back is not None
        and back.spend.llm_calls == 2
        and back.spend.cost_usd == __import__("decimal").Decimal("0.5")
    )
    assert UUID(int=0) != run.id


async def test_analytics_views_have_rows_after_a_run(db: Database) -> None:
    await _account(db)
    w = PgWorld(db)
    run = await w.start()
    await w.run(run)
    async with db.engine.connect() as conn:
        quality = (await conn.execute(text("SELECT sum(n), max(share) FROM analytics.v_claim_quality"))).one()
        cost = (
            await conn.execute(text("SELECT shown, cost_usd, cost_per_shown FROM analytics.v_run_cost"))
        ).one()
    assert quality[0] >= 3 and 0 < quality[1] <= 1
    assert cost[0] == 1 and cost[1] > 0 and cost[2] > 0


async def test_a2_resume_after_a_kill_during_analyse_repeats_nothing_completed(db: Database) -> None:
    from tests.fakes.discovery import AgentKilled

    await _account(db)
    w = PgWorld(db)
    run = await w.start()
    w.agents.kill_on_call = 6  # the GapAnalyst call: steps 1-5 are committed
    with pytest.raises(AgentKilled):
        await w.run(run)
    async with w.uow() as uow:
        mid = await uow.runs.get(run.id, OWNER)
    assert (
        mid is not None
        and mid.status is RunStatus.RUNNING
        and mid.step is not None
        and mid.step.value == "analyse"
    )
    calls_before = list(w.literature.calls)
    w.agents.kill_on_call = None
    assert (await w.run(run))["status"] == "done"
    assert w.agents.count("QueryPlanner") == 1 and w.literature.calls == calls_before
    async with w.uow() as uow:
        final = await uow.runs.get(run.id, OWNER)
    assert final is not None and final.generation == 2 and final.spend.llm_calls == 8
    assert await _count(db, "opportunity") == 2


async def test_an_overlong_model_quote_is_stored_clipped_and_marked_not_crashed(db: Database) -> None:
    from tests.unit.discovery import _world

    await _account(db)
    w = PgWorld(db)

    def long_quote(data: Any) -> Any:
        out = _world.analyst(data)
        first = out.opportunities[0]
        ref = first.claims[0].evidence_refs[0].model_copy(update={"quote": "lignin " * 80})
        refs = [ref, *first.claims[0].evidence_refs[1:]]
        claim = first.claims[0].model_copy(update={"evidence_refs": refs})
        opp = first.model_copy(update={"claims": [claim, *first.claims[1:]]})
        return out.model_copy(update={"opportunities": [opp, *out.opportunities[1:]]})

    w.agents.script("GapAnalyst", long_quote)
    run = await w.start()
    assert (await w.run(run))["status"] == "done"
    async with db.engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT check_code, char_length(quote) FROM evidence WHERE check_code = 'quote_too_long'"
                )
            )
        ).all()
    assert rows and all(r[1] <= 300 for r in rows)


async def test_a_readiness_flag_without_a_resolvable_ref_is_not_believed(db: Database) -> None:
    from tests.unit.discovery import _world

    await _account(db)
    w = PgWorld(db)

    def hostile(data: Any) -> Any:
        out = _world.analyst(data)
        items = [{"item": "Pilot line", "present_in_vault": True, "ref": "d9.aims[0]"}]
        from scout.discovery.domain.agent_io import ChecklistItem

        opps = [
            o.model_copy(update={"readiness_checklist": [ChecklistItem(**items[0])]})
            for o in out.opportunities
        ]
        return out.model_copy(update={"opportunities": opps})

    w.agents.script("GapAnalyst", hostile)
    run = await w.start()
    await w.run(run)
    async with w.uow() as uow:
        opps = await uow.opportunities.list_for_run(run.id)
    assert opps and all(not o.readiness_checklist[0]["present_in_vault"] for o in opps if o.scores)
    assert all(o.scores.readiness == 0.0 for o in opps if o.scores)
