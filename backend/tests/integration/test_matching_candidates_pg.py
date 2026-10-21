"""Candidate SQL, profile repository and conflict views on PostgreSQL (migrations 0008-0009)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from tests.integration.test_matching_repos_pg import proposal
from tests.integration.test_matching_reveal_pg import acc, seed_people

from scout.adapters.persistence.db import Database
from scout.matching.adapters.sql import SqlMatchUnitOfWork
from scout.matching.domain.profile import MatchProfile, TopicWeight
from scout.shared import AccountId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
A, B, C = AccountId(acc(1)), AccountId(acc(2)), AccountId(acc(3))
FIELD = "bio-based-materials"


async def make_profile(
    db: Database, account: AccountId, methods: dict[str, float], eligible: bool = True
) -> None:
    async with db.engine.connect() as conn:
        rid = (
            await conn.execute(text("SELECT id FROM researcher WHERE account_id = :a"), {"a": account})
        ).scalar_one()
    p = MatchProfile(
        account_id=account, researcher_id=rid, field=FIELD, region="europe", career_band="mid", activity=1.0,
        eligible=eligible, topics=(TopicWeight("t1", "T1", 1.0), TopicWeight("t2", "T2", 0.5)), methods=methods,
        method_sources={k: "abstract" for k in methods}, topics_hash="th", methods_hash="mh", vocabulary_version="1",
        methods_prompt_version="1.0.1", uses_vault_signals=False, computed_at=NOW,
    )  # fmt: skip
    async with SqlMatchUnitOfWork(db) as uow:
        await uow.profiles.save(p, [("new label", ("work:W1",))])
        await uow.commit()


async def candidates(
    db: Database, *, coauthors: bool = False, group: bool = False, account: AccountId | None = None
):
    async with SqlMatchUnitOfWork(db) as uow:
        return await uow.candidates.candidates(
            field=FIELD, account=account, exclude_coauthors=coauthors, exclude_same_group=group,
            cooldown_since=NOW - timedelta(days=180), limit=100,
        )  # fmt: skip


async def test_profile_round_trip_and_replace(db: Database) -> None:
    await seed_people(db, 1)
    await make_profile(db, A, {"lca": 1.0, "tea": 0.5})
    async with SqlMatchUnitOfWork(db) as uow:
        got = await uow.profiles.get(A)
        assert (
            got is not None
            and got.methods == {"lca": 1.0, "tea": 0.5}
            and [t.id for t in got.topics] == ["t1", "t2"]
        )
        assert await uow.profiles.accounts() == [A]
    await make_profile(db, A, {"nmr": 1.0})  # replaces rows and tolerates the same proposed label
    async with SqlMatchUnitOfWork(db) as uow:
        got = await uow.profiles.get(A)
        assert got is not None and got.methods == {"nmr": 1.0}
        await uow.profiles.delete(A)
        await uow.commit()
        assert await uow.profiles.get(A) is None


async def test_candidates_pool_conflicts_blocks_and_open_proposals(db: Database) -> None:
    await seed_people(db, 3)
    for a, m in ((A, {"lca": 1.0}), (B, {"tea": 1.0}), (C, {"nmr": 1.0})):
        await make_profile(db, a, m)
    assert await candidates(db) == [(A, B), (A, C), (B, C)]
    assert await candidates(db, account=C) == [(A, C), (B, C)]
    assert await candidates(db, group=True) == []  # seed_people gives everyone the same institution
    async with db.engine.begin() as conn:
        for acct, wid in ((A, "W1"), (B, "W1")):
            rid = (
                await conn.execute(text("SELECT id FROM researcher WHERE account_id = :a"), {"a": acct})
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO claimed_work (id, researcher_id, openalex_work_id, title) VALUES (:i, :r, :w, 't')"
                ),
                {"i": uuid4(), "r": rid, "w": wid},
            )
    assert (A, B) not in await candidates(db, coauthors=True) and (A, C) in await candidates(
        db, coauthors=True
    )
    assert (A, B) in await candidates(db, coauthors=False)
    async with SqlMatchUnitOfWork(db) as uow:
        await uow.blocks.add(C, A, uuid4(), NOW)
        await uow.matches.add(proposal(B, C))
        await uow.commit()
    assert await candidates(db) == [(A, B)]  # A-C blocked (either direction), B-C has an open proposal


async def test_ineligible_or_unconsenting_accounts_are_not_in_the_pool(db: Database) -> None:
    await seed_people(db, 2)
    await make_profile(db, A, {"lca": 1.0})
    await make_profile(db, B, {"tea": 1.0}, eligible=False)
    assert await candidates(db) == []
    await make_profile(db, B, {"tea": 1.0})
    assert await candidates(db) == [(A, B)]
    async with db.engine.begin() as conn:
        await conn.execute(text("UPDATE account SET consent_matching = false WHERE id = :a"), {"a": B})
    assert await candidates(db) == []
    async with SqlMatchUnitOfWork(db) as uow:
        assert await uow.matches.open_counts([A, B], NOW - timedelta(days=7)) == {A: 0, B: 0}
        assert await uow.matches.pending_outside_pool(10) == []
