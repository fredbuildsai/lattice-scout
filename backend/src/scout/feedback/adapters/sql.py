"""SQL repositories and unit of work of the feedback context."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.feedback.domain.feedback import Feedback, Rating, Reason, TargetType
from scout.shared import AccountId


def _from_row(r: Any) -> Feedback:
    return Feedback(
        id=r.id, author_id=AccountId(r.author_id), target_type=TargetType(r.target_type), target_id=r.target_id,
        rating=Rating(r.rating) if r.rating else None, stars=r.stars, reasons=tuple(Reason(x) for x in r.reasons), comment=r.comment,
        created_at=r.created_at, updated_at=r.updated_at,
    )  # fmt: skip


class SqlFeedbackRepo:
    """Implements ``FeedbackRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert(self, feedback: Feedback) -> Feedback:
        """Insert or replace (rating, reasons, comment, ``updated_at``); ``created_at`` is kept."""
        t = m.feedback
        values = dict(
            id=feedback.id, author_id=feedback.author_id, target_type=feedback.target_type.value,
            target_id=feedback.target_id, rating=feedback.rating.value if feedback.rating else None, stars=feedback.stars,
            reasons=[r.value for r in feedback.reasons], comment=feedback.comment,
            created_at=feedback.created_at, updated_at=feedback.updated_at,
        )  # fmt: skip
        ins = pg_insert(t).values(**values)
        stmt = ins.on_conflict_do_update(
            index_elements=[t.c.author_id, t.c.target_type, t.c.target_id],
            set_={k: ins.excluded[k] for k in ("rating", "stars", "reasons", "comment", "updated_at")},
        ).returning(t)
        return _from_row((await self._s.execute(stmt)).one())

    async def get(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> Feedback | None:
        """The author's rating of the target."""
        t = m.feedback
        row = (
            await self._s.execute(
                select(t).where(
                    t.c.author_id == author_id,
                    t.c.target_type == target_type.value,
                    t.c.target_id == target_id,
                )
            )
        ).first()
        return _from_row(row) if row else None


class SqlFeedbackUnitOfWork(SqlUnitOfWork):
    """Unit of work over one session."""

    _feedback: SqlFeedbackRepo

    def _bind(self) -> None:
        self._feedback = SqlFeedbackRepo(self.session)

    @property
    def feedback(self) -> SqlFeedbackRepo:
        """Ratings."""
        return self._feedback
