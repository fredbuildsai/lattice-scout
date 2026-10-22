"""``GiveFeedback`` use case and the read of one's own rating."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from uuid import UUID

from scout.feedback.domain.feedback import Feedback, Rating, Reason, TargetType
from scout.feedback.ports.repos import FeedbackUnitOfWork, TargetCheck
from scout.shared import AccountId, Clock, NotFound


class Unset(Enum):
    """The caller did not send the field: keep what is stored."""

    UNSET = "unset"


UNSET = Unset.UNSET


class GiveFeedback:
    """Rate an opportunity or a match. Rating again replaces the earlier rating.

    ``reasons=None`` and ``comment=UNSET`` keep the earlier values (a thumb click must not erase them);
    ``reasons=[]`` and ``comment=None`` or ``""`` clear them.
    """

    def __init__(self, uow: Callable[[], FeedbackUnitOfWork], clock: Clock, targets: TargetCheck) -> None:
        self._uow, self._clock, self._targets = uow, clock, targets

    async def execute(
        self,
        author_id: AccountId,
        target_type: TargetType,
        target_id: UUID,
        rating: Rating | None,
        reasons: Sequence[Reason] | None = None,
        comment: str | Unset | None = UNSET,
        stars: int | None = None,
    ) -> Feedback:
        """Store the rating.

        Raises:
            NotFound: the target is unknown, not yours, or of a kind that cannot be rated yet.
            ValidationFailed: the comment is too long.
        """
        now = self._clock.now()
        # Cheap validation first (422 beats 404): kind, reasons, comment length.
        Feedback.give(
            author_id, target_type, target_id, rating, tuple(reasons or ()),
            None if isinstance(comment, Unset) else comment, now, stars=stars,
        )  # fmt: skip
        # The other contexts answer first (through their application services), outside our transaction.
        if not await self._targets.rateable(author_id, target_type, target_id):
            raise NotFound("feedback_target_not_found")
        async with self._uow() as uow:
            earlier = await uow.feedback.get(author_id, target_type, target_id)
            keep_reasons = reasons is None and earlier is not None
            keep_comment = isinstance(comment, Unset) and earlier is not None
            fb = Feedback.give(
                author_id, target_type, target_id, rating,
                earlier.reasons if keep_reasons and earlier else tuple(reasons or ()),
                earlier.comment if keep_comment and earlier else (None if isinstance(comment, Unset) else comment),
                now, stars=stars,
            )  # fmt: skip
            stored = await uow.feedback.upsert(fb)
            await uow.commit()
        return stored

    async def mine(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> Feedback | None:
        """The author's current rating of the target."""
        async with self._uow() as uow:
            return await uow.feedback.get(author_id, target_type, target_id)
