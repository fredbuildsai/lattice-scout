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
    """Closed list of reason chips (the screens W5/W6 and M5/M6 show the same five)."""

    SPOT_ON = "spot_on"
    ALREADY_KNEW = "already_knew"
    NOT_FEASIBLE = "not_feasible"
    WRONG_FIELD = "wrong_field"
    TOO_VAGUE = "too_vague"


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
    rating: Rating
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
        rating: Rating,
        reasons: tuple[Reason, ...] | list[Reason],
        comment: str | None,
        now: datetime,
        created_at: datetime | None = None,
    ) -> Feedback:
        """Build a valid rating: reasons de-duplicated in input order (at most 5), comment trimmed and bounded.

        Raises:
            ValidationFailed: the comment is longer than 1000 characters.
        """
        text = (comment or "").strip()
        if len(text) > MAX_COMMENT:
            raise ValidationFailed("comment_too_long", extra={"max": MAX_COMMENT})
        return cls(
            id=feedback_id(author_id, target_type, target_id), author_id=author_id, target_type=target_type,
            target_id=target_id, rating=rating, reasons=tuple(dict.fromkeys(reasons)),
            comment=clean_text(text, MAX_COMMENT) if text else None,
            created_at=created_at or now, updated_at=now,
        )  # fmt: skip
