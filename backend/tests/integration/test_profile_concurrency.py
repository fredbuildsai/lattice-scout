"""Real interleavings (review M1 R2-2/R2-4): an import that is blocked mid-way while users toggle, reclaim or
claim, against PostgreSQL. These fail if the row/advisory locks or the non-overwriting upsert are removed."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.persistence.db import Database
from scout.adapters.queue.postgres import PostgresJobQueue
from scout.identity.adapters.sql import SqlIdentityUnitOfWork
from scout.identity.application.service import IdentityService
from scout.ports.literature import AuthorCandidate, AuthorRecord, WorkQuery, WorkRecord
from scout.profile.adapters.sql import SqlProfileUnitOfWork
from scout.profile.application.import_profile import ImportProfile
from scout.profile.application.service import ProfileService
from scout.shared import AccountId, Conflict, SystemClock, UuidGenerator

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3] / "tests/fixtures/openalex"
FOSTER, SPLIT = "A5059588273", "A5140167166"
A, B = AccountId(UUID(int=11)), AccountId(UUID(int=12))


class BlockingLiterature:
    """Fixture literature whose ``list_author_works(block_on)`` waits until released."""

    def __init__(self, block_on: str) -> None:
        self.inner, self.block_on = FixtureLiterature(ROOT), block_on
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        if author_id == self.block_on:
            self.reached.set()
            await self.release.wait()
        return await self.inner.list_author_works(author_id, limit)

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        return await self.inner.search_authors(name, affiliation_hint)

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        return await self.inner.authors_by_orcid(orcid)

    async def get_author(self, author_id: str) -> AuthorRecord:
        return await self.inner.get_author(author_id)

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        return await self.inner.search_works(query)

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        return await self.inner.get_work(work_id_or_doi)

    async def count_works(self, query: WorkQuery) -> int:
        return await self.inner.count_works(query)


@dataclass
class Env:
    profile: ProfileService
    importer: ImportProfile
    lit: BlockingLiterature
    db: Database


async def _env(db: Database, block_on: str) -> Env:
    clock, ids = SystemClock(), UuidGenerator()
    lit = BlockingLiterature(block_on)
    ident = IdentityService(lambda: SqlIdentityUnitOfWork(db), clock, ids, "bio")
    for acct, email in ((A, "ada@example.edu"), (B, "bob@example.edu")):
        await ident.ensure_account(acct, email)
    queue = PostgresJobQueue(db, clock, ids)
    uow = lambda: SqlProfileUnitOfWork(db)  # noqa: E731
    return Env(ProfileService(uow, lit, queue, clock, ids), ImportProfile(uow, lit, clock, ids), lit, db)


async def _sql(db: Database, q: str, **p: object) -> list[tuple[object, ...]]:
    async with db.engine.connect() as conn:
        return [tuple(r) for r in (await conn.execute(text(q), p)).all()]


async def test_toggle_while_import_is_blocked_survives(db: Database) -> None:
    env = await _env(db, block_on=SPLIT)
    r = await env.profile.claim(A, [FOSTER, SPLIT], FOSTER, None)
    task = asyncio.create_task(
        env.importer.handle({"researcher_id": str(r.id), "generation": 1}, last_attempt=False)
    )
    await asyncio.wait_for(env.lit.reached.wait(), 10)  # FOSTER's works are committed, SPLIT is in flight
    work = next(w for w in (await env.profile.get_profile(A)).works if w.included)
    await env.profile.toggle_work(A, work.id, False)  # commits while the import is blocked
    env.lit.release.set()
    await asyncio.wait_for(task, 20)
    again = next(w for w in (await env.profile.get_profile(A)).works if w.id == work.id)
    assert again.included is False and again.included_by_user is True


async def test_reclaim_during_import_makes_the_old_job_a_noop(db: Database) -> None:
    env = await _env(db, block_on=SPLIT)
    r = await env.profile.claim(A, [FOSTER, SPLIT], FOSTER, None)
    task = asyncio.create_task(
        env.importer.handle({"researcher_id": str(r.id), "generation": 1}, last_attempt=False)
    )
    await asyncio.wait_for(env.lit.reached.wait(), 10)
    r2 = await env.profile.claim(A, [FOSTER], FOSTER, None)  # new generation while the old import runs
    assert r2.import_generation == 2
    env.lit.release.set()
    out = await asyncio.wait_for(task, 20)
    assert out == {"skipped": "stale"}
    rows = await _sql(db, "SELECT import_generation, import_status FROM researcher")
    assert rows == [(2, "queued")]  # the stale job did not clobber the new claim
    ids = await _sql(db, "SELECT openalex_author_id FROM researcher_openalex_id")
    assert ids == [(FOSTER,)]


async def test_claim_does_not_clobber_other_accounts_import(db: Database) -> None:
    env = await _env(db, block_on=SPLIT)
    ra = await env.profile.claim(A, [FOSTER, SPLIT], FOSTER, None)
    task = asyncio.create_task(
        env.importer.handle({"researcher_id": str(ra.id), "generation": 1}, last_attempt=False)
    )
    await asyncio.wait_for(env.lit.reached.wait(), 10)
    await env.profile.claim(B, [FOSTER], FOSTER, None)  # shares an id with A: marks A contested
    env.lit.release.set()
    await asyncio.wait_for(task, 20)
    rows = await _sql(
        db, "SELECT import_status, verified_level, import_generation FROM researcher WHERE id = :i", i=ra.id
    )
    assert rows == [("done", "contested", 1)]  # import result and checkpoints survived the other claim
    topics = await _sql(db, "SELECT count(*) FROM researcher_topic WHERE researcher_id = :i", i=ra.id)
    assert int(str(topics[0][0])) > 0


async def test_concurrent_first_claims_of_one_account_give_one_profile_and_a_409(db: Database) -> None:
    env = await _env(db, block_on="none")
    results = await asyncio.gather(
        env.profile.claim(A, [FOSTER], FOSTER, None),
        env.profile.claim(A, [SPLIT], SPLIT, None),
        return_exceptions=True,
    )
    assert any(not isinstance(x, BaseException) for x in results)
    assert all(not isinstance(x, BaseException) or isinstance(x, Conflict) for x in results), results
    assert (await _sql(db, "SELECT count(*) FROM researcher"))[0][0] == 1


async def test_crossing_claims_do_not_deadlock(db: Database) -> None:
    env = await _env(db, block_on="none")
    await env.profile.claim(A, [FOSTER], FOSTER, None)
    await env.profile.claim(B, [SPLIT], SPLIT, None)
    results = await asyncio.wait_for(
        asyncio.gather(
            env.profile.claim(A, [SPLIT], SPLIT, None), env.profile.claim(B, [FOSTER], FOSTER, None),
            return_exceptions=True,
        ),
        20,
    )  # fmt: skip
    assert not any(isinstance(x, BaseException) for x in results), results


def _block_inside_import_transaction(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Make the importer wait *between its reads and its writes* (inside the unit of work, after the row lock)."""
    from scout.profile.adapters import sql

    inside, release = asyncio.Event(), asyncio.Event()
    original = sql.SqlWorkRepo.upsert_many
    state = {"first": True}

    async def slow(self: sql.SqlWorkRepo, works: list[object]) -> None:
        if state["first"]:
            state["first"] = False
            inside.set()
            await release.wait()
        await original(self, works)  # type: ignore[arg-type]

    monkeypatch.setattr(sql.SqlWorkRepo, "upsert_many", slow)
    return inside, release


async def test_reclaim_waits_for_the_importers_row_lock_and_wins(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await _env(db, block_on="none")
    r = await env.profile.claim(A, [FOSTER, SPLIT], FOSTER, None)
    inside, release = _block_inside_import_transaction(monkeypatch)
    imp = asyncio.create_task(
        env.importer.handle({"researcher_id": str(r.id), "generation": 1}, last_attempt=False)
    )
    await asyncio.wait_for(inside.wait(), 10)
    reclaim = asyncio.create_task(env.profile.claim(A, [FOSTER], FOSTER, None))
    await asyncio.sleep(0.5)
    assert not reclaim.done()  # blocked on the researcher row lock held by the import's transaction
    release.set()
    await asyncio.wait_for(asyncio.gather(imp, reclaim), 30)
    rows = await _sql(db, "SELECT import_generation, import_status FROM researcher")
    assert rows == [(2, "queued")]  # the importer's stale copy did not overwrite the newer claim
    assert await _sql(db, "SELECT openalex_author_id FROM researcher_openalex_id") == [(FOSTER,)]


async def test_toggle_waits_for_the_importer_and_is_applied_after_it(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = await _env(db, block_on="none")
    r = await env.profile.claim(A, [FOSTER], FOSTER, None)
    await env.importer.handle({"researcher_id": str(r.id), "generation": 1}, last_attempt=False)
    work = next(w for w in (await env.profile.get_profile(A)).works if w.included)
    r2 = await env.profile.claim(A, [FOSTER, SPLIT], FOSTER, None)
    inside, release = _block_inside_import_transaction(monkeypatch)
    imp = asyncio.create_task(
        env.importer.handle(
            {"researcher_id": str(r.id), "generation": r2.import_generation}, last_attempt=False
        )
    )
    await asyncio.wait_for(inside.wait(), 10)
    toggle = asyncio.create_task(env.profile.toggle_work(A, work.id, False))
    await asyncio.sleep(0.5)
    assert not toggle.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(imp, toggle), 30)
    assert (
        await _sql(db, "SELECT included, included_by_user FROM claimed_work WHERE id = :i", i=work.id)
    ) == [(False, True)]
