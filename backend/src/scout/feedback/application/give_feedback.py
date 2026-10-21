"""``GiveFeedback`` use case and the read of one's own rating."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from scout.feedback.domain.feedback import Feedback, Rating, Reason, TargetType
from scout.feedback.ports.repos import FeedbackUnitOfWork
from scout.shared import AccountId, Clock, NotFound


class GiveFeedback:
    """Rate an opportunity or a match. Rating again replaces the earlier rating."""

    def __init__(self, uow: Callable[[], FeedbackUnitOfWork], clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    async def execute(
        self,
        author_id: AccountId,
        target_type: TargetType,
        target_id: UUID,
        rating: Rating,
        reasons: Sequence[Reason] = (),
        comment: str | None = None,
    ) -> Feedback:
        """Store the rating.

        Raises:
            NotFound: the target is unknown, not yours, or of a kind that cannot be rated yet.
            ValidationFailed: the comment is too long.
        """
        now = self._clock.now()
        fb = Feedback.give(author_id, target_type, target_id, rating, tuple(reasons), comment, now)
        async with self._uow() as uow:
            if not await uow.targets.rateable(author_id, target_type, target_id):
                raise NotFound("feedback_target_not_found")
            stored = await uow.feedback.upsert(fb)
            await uow.commit()
        return stored

    async def mine(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> Feedback | None:
        """The author's current rating of the target."""
        async with self._uow() as uow:
            return await uow.feedback.get(author_id, target_type, target_id)
