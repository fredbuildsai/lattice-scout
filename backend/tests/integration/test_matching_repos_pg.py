"""Matching repositories on PostgreSQL: round trip, duplicate guard, locking reads, reveal only when mutual."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.integration.test_matching_reveal_pg import acc, seed_people

from scout.adapters.persistence.db import Database
from scout.matching.adapters.sql import SqlMatchUnitOfWork
from scout.matching.domain.enums import Action, MatchState
from scout.matching.domain.ids import match_id
from scout.matching.domain.proposal import MatchProposal, PublicFacet, ScoreBasis, StoredRationale
from scout.shared import AccountId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
A, B, C = AccountId(acc(1)), AccountId(acc(2)), AccountId(acc(3))


def proposal(a=A, b=B, week="2026-W43") -> MatchProposal:
    return MatchProposal.propose(
        id=match_id(a, b, week), a_id=a, b_id=b, field="bio-based-materials", score=0.4,
        score_basis=ScoreBasis(topic_overlap=0.5, method_overlap=0.1, activity=0.9, alpha=1.0, beta=1.5,
                               shared_topics=["t"], a_only=["m1"], b_only=["m2"]),
        rationale=StoredRationale(source="template", why="because"),
        a_public=PublicFacet(), b_public=PublicFacet(), now=NOW, expiry_days=14,
    )  # fmt: skip


async def test_round_trip_duplicates_and_reveal(db: Database) -> None:
    await seed_people(db, 3)
    p = proposal()
    async with SqlMatchUnitOfWork(db) as uow:
        assert await uow.matches.add(p) is True
        assert await uow.matches.add(proposal(week="2026-W44")) is False  # same active pair
        await uow.commit()
    async with SqlMatchUnitOfWork(db) as uow:
        got = await uow.matches.get(p.id, A)
        assert got is not None and got.rationale.why == "because" and got.state is MatchState.PROPOSED
        assert await uow.matches.get(p.id, C) is None  # not a participant
        assert await uow.reveal.contact(p.id, A) is None  # not mutual
        got.decide(A, Action.ACCEPT, NOW)
        await uow.matches.save(got)
        await uow.commit()
    async with SqlMatchUnitOfWork(db) as uow:
        got = await uow.matches.get_for_update(p.id, B)
        assert got is not None and got.state is MatchState.A_ACCEPTED
        got.decide(B, Action.ACCEPT, NOW + timedelta(hours=1))
        await uow.matches.save(got)
        await uow.commit()
    async with SqlMatchUnitOfWork(db) as uow:
        contact = await uow.reveal.contact(p.id, A)
        assert contact is not None and contact.display_name == "Person 2 (sample)"
        assert contact.openalex_ids == ["A0002"]
        assert [x.id for x in await uow.matches.list_for(B, 10, 0)] == [p.id]
        assert await uow.matches.count_created_since(A, NOW - timedelta(days=7)) == 1
        assert await uow.matches.due_for_expiry(NOW + timedelta(days=30), 10) == []  # mutual never expires
        await uow.blocks.add(A, B, p.id, NOW)
        await uow.blocks.add(A, B, p.id, NOW)  # idempotent
        await uow.commit()


async def test_due_for_expiry_lists_pending_only(db: Database) -> None:
    await seed_people(db, 2)
    p = proposal()
    async with SqlMatchUnitOfWork(db) as uow:
        await uow.matches.add(p)
        await uow.commit()
    async with SqlMatchUnitOfWork(db) as uow:
        assert await uow.matches.due_for_expiry(NOW + timedelta(days=1), 10) == []
        assert await uow.matches.due_for_expiry(NOW + timedelta(days=15), 10) == [p.id]
