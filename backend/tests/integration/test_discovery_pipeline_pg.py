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
    assert final.spend.llm_calls == 8 and final.spend.openalex_calls == 4 and final.spend.cost_usd > 0
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
