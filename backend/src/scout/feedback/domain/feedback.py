"""``Feedback``: one user's rating of one opportunity (or, from M5, one match). Latest rating wins."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from scout.shared import AccountId, ValidationFailed
from scout.shared.text import clean_text

FEEDBACK_NS = UUID("6d1a0c52-4f7e-5b39-8a26-e9b3c7d41f08")
MAX_COMMENT = 1000


class TargetType(StrEnum):
    """What is rated."""

    OPPORTUNITY = "opportunity"
    MATCH = "match"


class Rating(StrEnum):
    """Thumbs up or down."""

    UP = "up"
    DOWN = "down"


class Reason(StrEnum):
    """Closed list of reason chips: five for opportunities (W5/W6), five for matches (W7)."""

    SPOT_ON = "spot_on"
    ALREADY_KNEW = "already_knew"
    NOT_FEASIBLE = "not_feasible"
    WRONG_FIELD = "wrong_field"
    TOO_VAGUE = "too_vague"
    METHODS_FIT = "methods_fit"
    RIGHT_SENIORITY = "right_seniority"
    SHARED_TIMELINE = "shared_timeline"
    TOO_SIMILAR = "too_similar"
    ALREADY_KNEW_THEM = "already_knew_them"


OPPORTUNITY_REASONS = frozenset(
    {Reason.SPOT_ON, Reason.ALREADY_KNEW, Reason.NOT_FEASIBLE, Reason.WRONG_FIELD, Reason.TOO_VAGUE}
)
MATCH_REASONS = frozenset(
    {
        Reason.METHODS_FIT,
        Reason.RIGHT_SENIORITY,
        Reason.SHARED_TIMELINE,
        Reason.TOO_SIMILAR,
        Reason.ALREADY_KNEW_THEM,
    }
)
MAX_REASONS = 5


def allowed_reasons(target_type: TargetType) -> frozenset[Reason]:
    """The chips that make sense for a target type."""
    return OPPORTUNITY_REASONS if target_type is TargetType.OPPORTUNITY else MATCH_REASONS


def feedback_id(author: UUID, target_type: TargetType, target_id: UUID) -> UUID:
    """One row per author and target: rating again replaces the earlier rating."""
    return uuid5(FEEDBACK_NS, f"{author}:{target_type.value}:{target_id}")


@dataclass(frozen=True)
class Feedback:
    """A rating with optional reasons and comment."""

    id: UUID
    author_id: AccountId
    target_type: TargetType
    target_id: UUID
    rating: Rating | None
    stars: int | None
    reasons: tuple[Reason, ...]
    comment: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def give(
        cls,
        author_id: AccountId,
        target_type: TargetType,
        target_id: UUID,
        rating: Rating | None,
        reasons: tuple[Reason, ...] | list[Reason],
        comment: str | None,
        now: datetime,
        created_at: datetime | None = None,
        stars: int | None = None,
    ) -> Feedback:
        """Build a valid rating: reasons de-duplicated in input order (at most 5), comment trimmed and bounded.

        Raises:
            ValidationFailed: the comment is too long, an opportunity has no up/down rating, a match has no
                1-5 stars, a reason does not belong to the target type, or there are more than 5 reasons.
        """
        if target_type is TargetType.OPPORTUNITY and (rating is None or stars is not None):
            raise ValidationFailed("opportunities_take_a_rating")
        if target_type is TargetType.MATCH and (stars is None or rating is not None or not 1 <= stars <= 5):
            raise ValidationFailed("matches_take_1_to_5_stars")
        unique = tuple(dict.fromkeys(reasons))
        if any(r not in allowed_reasons(target_type) for r in unique):
            raise ValidationFailed("reason_not_for_this_target")
        if len(unique) > MAX_REASONS:
            raise ValidationFailed("too_many_reasons", extra={"max": MAX_REASONS})
        text = (comment or "").strip()
        if len(text) > MAX_COMMENT:
            raise ValidationFailed("comment_too_long", extra={"max": MAX_COMMENT})
        return cls(
            id=feedback_id(author_id, target_type, target_id), author_id=author_id, target_type=target_type,
            target_id=target_id, rating=rating, stars=stars, reasons=unique,
            comment=clean_text(text, MAX_COMMENT) if text else None,
            created_at=created_at or now, updated_at=now,
        )  # fmt: skip
