"""Feedback on PostgreSQL (migration 0007): upsert, target check, constraints and the admin summary view."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scout.adapters.persistence.db import Database
from scout.feedback.adapters.sql import SqlFeedbackUnitOfWork
from scout.feedback.application.give_feedback import GiveFeedback
from scout.feedback.domain.feedback import Rating, Reason, TargetType
from scout.shared import AccountId, FixedClock, NotFound

pytestmark = pytest.mark.integration

ME = AccountId(UUID(int=1))
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


async def _seed(db: Database, opp: UUID) -> None:
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                " created_at, updated_at) VALUES (:id, 'o@example.edu', false, false, 'f', 'user', now(), now())"
            ),
            {"id": ME},
        )
        run = uuid4()
        await conn.execute(
            text(
                "INSERT INTO analysis_run (id, owner_id, status, budget) VALUES (:r, :o, 'done', '{}'::jsonb)"
            ),
            {"r": run, "o": ME},
        )
        await conn.execute(
            text(
                "INSERT INTO opportunity (id, run_id, owner_id, batch, ord, kind, title, one_line, status,"
                " cluster_ids, score_basis, readiness_checklist, next_steps, verify_state, created_at, updated_at)"
                " VALUES (:i, :r, :o, 0, 0, 'white_spot', 't', 'l', 'shown', '{}', '{}'::jsonb, '[]'::jsonb,"
                " '[]'::jsonb, 'done', now(), now())"
            ),
            {"i": opp, "r": run, "o": ME},
        )


class _OwnOpportunities:
    """Target check stub: the visibility rule is tested through the API (test_feedback_api.py)."""

    def __init__(self, opp: UUID) -> None:
        self._opp = opp

    async def rateable(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> bool:
        return target_type is TargetType.OPPORTUNITY and target_id == self._opp and author_id == ME


async def test_upsert_target_check_and_summary_view(db: Database) -> None:
    opp = uuid4()
    await _seed(db, opp)
    uc = GiveFeedback(lambda: SqlFeedbackUnitOfWork(db), FixedClock(NOW), _OwnOpportunities(opp))
    await uc.execute(ME, TargetType.OPPORTUNITY, opp, Rating.UP, [Reason.SPOT_ON])
    second = await uc.execute(
        ME, TargetType.OPPORTUNITY, opp, Rating.DOWN, [Reason.TOO_VAGUE, Reason.WRONG_FIELD], "meh"
    )
    assert second.rating is Rating.DOWN
    mine = await uc.mine(ME, TargetType.OPPORTUNITY, opp)
    assert (
        mine is not None and mine.reasons == (Reason.TOO_VAGUE, Reason.WRONG_FIELD) and mine.comment == "meh"
    )
    with pytest.raises(NotFound):
        await uc.execute(ME, TargetType.OPPORTUNITY, uuid4(), Rating.UP)
    with pytest.raises(NotFound):
        await uc.execute(ME, TargetType.MATCH, opp, None, stars=3)
    async with db.engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT rating, reason, n FROM analytics.v_feedback_summary ORDER BY reason")
            )
        ).all()
        assert [tuple(r) for r in rows] == [("down", "too_vague", 1), ("down", "wrong_field", 1)]
        assert (await conn.execute(text("SELECT count(*) FROM feedback"))).scalar_one() == 1


async def test_database_rejects_unknown_reasons(db: Database) -> None:
    await _seed(db, uuid4())
    with pytest.raises(DBAPIError):
        async with db.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO feedback (id, author_id, target_type, target_id, rating, reasons)"
                    " VALUES (gen_random_uuid(), :a, 'opportunity', gen_random_uuid(), 'up', ARRAY['bogus'])"
                ),
                {"a": ME},
            )


@pytest.mark.parametrize(
    ("target", "extra", "reasons"),
    [
        ("opportunity", "rating", "ARRAY['methods_fit']"),  # a match chip on an opportunity
        ("match", "stars", "ARRAY['spot_on']"),  # an opportunity chip on a match
        (
            "opportunity",
            "rating",
            "ARRAY['spot_on','already_knew','not_feasible','wrong_field','too_vague','spot_on']",
        ),
    ],
)
async def test_database_checks_reasons_per_target_and_the_limit_of_five(
    db: Database, target: str, extra: str, reasons: str
) -> None:
    await _seed(db, uuid4())
    value = "'up'" if extra == "rating" else "3"
    with pytest.raises(DBAPIError):
        async with db.engine.begin() as conn:
            await conn.execute(
                text(
                    f"INSERT INTO feedback (id, author_id, target_type, target_id, {extra}, reasons)"  # noqa: S608
                    f" VALUES (gen_random_uuid(), :a, '{target}', gen_random_uuid(), {value}, {reasons})"
                ),
                {"a": ME},
            )
