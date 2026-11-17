"""External suggestions on PostgreSQL: round trip, ordering, no duplicates, owner-only reads, refresh log, erasure,
RLS on the new tables, and the claimed author ids of the profile repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.integration.test_matching_reveal_pg import acc, run, seed_people

from scout.adapters.persistence.db import Database
from scout.matching.adapters.sql import SqlMatchUnitOfWork
from scout.matching.application.erasure import MatchErasure
from scout.matching.domain.external import (
    ExternalAuthor,
    ExternalLayer,
    ExternalSuggestion,
    ScoredExternal,
    SuggestionState,
)
from scout.profile.adapters.sql import SqlProfileUnitOfWork
from scout.shared import AccountId

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
A, B = AccountId(acc(1)), AccountId(acc(2))


def suggestion(account: AccountId, oa: str, layer: ExternalLayer, score: float) -> ExternalSuggestion:
    author = ExternalAuthor(
        id=oa, name=f"Name {oa}", institution="Uni", h_index=5, citations=10**10, works_count=9
    )
    scored = ScoredExternal(author, layer, ("Bea",) if layer is ExternalLayer.IN_NETWORK else (), score, score,
                            ("Lignin",), 3)  # fmt: skip
    return ExternalSuggestion.from_scored(scored, id=uuid4(), account_id=account, now=NOW)


async def test_round_trip_order_duplicates_owner_and_erasure(db: Database) -> None:
    await seed_people(db, 2)
    rows = [
        suggestion(A, "F1", ExternalLayer.IN_FIELD, 0.9),
        suggestion(A, "N1", ExternalLayer.IN_NETWORK, 0.2),
        suggestion(A, "F2", ExternalLayer.IN_FIELD, 0.5),
        suggestion(B, "F1", ExternalLayer.IN_FIELD, 0.4),
    ]
    async with SqlMatchUnitOfWork(db) as uow:
        assert await uow.external.add_many(rows) == 4
        assert await uow.external.add_many([suggestion(A, "F1", ExternalLayer.IN_FIELD, 0.1)]) == 0  # seen
        await uow.external.record_refresh(A, NOW - timedelta(days=2))
        await uow.external.record_refresh(A, NOW)
        await uow.commit()
    visible = (SuggestionState.ACTIVE, SuggestionState.SAVED)
    async with SqlMatchUnitOfWork(db) as uow:
        got = await uow.external.list_for(A, visible)
        assert [s.openalex_id for s in got] == ["N1", "F1", "F2"]  # network first, then score
        n1 = got[0]
        assert n1.vias == ("Bea",) and n1.shared_topics == ("Lignin",) and n1.citations == 10**10
        assert n1.formula == "ext1" and n1.layer is ExternalLayer.IN_NETWORK and n1.created_at == NOW
        assert [s.openalex_id for s in await uow.external.list_for(A, visible, ExternalLayer.IN_FIELD)] == [
            "F1",
            "F2",
        ]
        assert await uow.external.seen_ids(A) == {"N1", "F1", "F2"}
        assert await uow.external.get(n1.id, B) is None  # not yours
        assert await uow.external.refreshes_since(A, NOW - timedelta(days=1)) == 1
        n1.set_state(SuggestionState.DISMISSED, NOW + timedelta(hours=1))
        await uow.external.save(n1)
        await uow.commit()
    async with SqlMatchUnitOfWork(db) as uow:
        assert [s.openalex_id for s in await uow.external.list_for(A, visible)] == ["F1", "F2"]
        again = await uow.external.get(n1.id, A)
        assert again is not None and again.state is SuggestionState.DISMISSED
    result = await MatchErasure(lambda: SqlMatchUnitOfWork(db)).forget(A, NOW)
    assert result.external_deleted == 3
    left = await run(db, "SELECT account_id FROM match_external_suggestion")
    assert [r[0] for r in left] == [B]
    assert await run(db, "SELECT 1 FROM match_external_refresh WHERE account_id = :a", a=A) == []


async def test_rls_on_new_tables_and_claimed_author_ids(db: Database) -> None:
    await seed_people(db, 2)
    rls = await run(
        db,
        "SELECT relname, relrowsecurity FROM pg_class WHERE relname IN ('match_external_suggestion','match_external_refresh')",
    )
    assert sorted(rls) == [("match_external_refresh", True), ("match_external_suggestion", True)]
    async with SqlProfileUnitOfWork(db) as uow:
        assert await uow.researchers.all_author_ids() == {"A0001", "A0002"}
