"""Feedback persistence ports."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from scout.feedback.domain.feedback import Feedback, TargetType
from scout.shared import AccountId, UnitOfWork


class TargetCheck(Protocol):
    """Whether a target may be rated by the author, answered by the context that owns it (visible opportunity,
    mutual match). An unseen target is the same as an unknown one."""

    async def rateable(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> bool:
        """True when ``author_id`` may rate the target."""
        ...


class FeedbackRepo(Protocol):
    """Ratings, one per author and target."""

    async def upsert(self, feedback: Feedback) -> Feedback:
        """Insert, or replace the author's earlier rating of the same target (``created_at`` is kept).

        Returns the stored row.
        """
        ...

    async def get(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> Feedback | None:
        """The author's rating of the target, or None."""
        ...


class FeedbackUnitOfWork(UnitOfWork, Protocol):
    """One transaction over the feedback repository."""

    @property
    def feedback(self) -> FeedbackRepo:
        """Ratings."""
        ...
