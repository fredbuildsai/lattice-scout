"""DTOs of the feedback endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReasonName = Literal[
    "spot_on", "already_knew", "not_feasible", "wrong_field", "too_vague",
    "methods_fit", "right_seniority", "shared_timeline", "too_similar", "already_knew_them",
]  # fmt: skip


class FeedbackIn(BaseModel):
    """A rating. Rating the same target again replaces the earlier one."""

    target_type: Literal["opportunity", "match"]
    target_id: UUID
    rating: Literal["up", "down"] | None = None
    stars: int | None = Field(default=None, ge=1, le=5)
    reasons: list[ReasonName] | None = Field(
        default=None, max_length=5, description="Omit to keep the earlier chips; [] clears them."
    )
    comment: str | None = Field(
        default=None, max_length=1000, description="Omit to keep the earlier comment; null or '' clears it."
    )


class FeedbackOut(BaseModel):
    """The stored rating."""

    target_type: Literal["opportunity", "match"]
    target_id: UUID
    rating: Literal["up", "down"] | None
    stars: int | None
    reasons: list[ReasonName]
    comment: str | None
    updated_at: datetime


class MatchRatingIn(BaseModel):
    """Rate a mutual match from 1 to 5 stars."""

    stars: int = Field(ge=1, le=5)
    reasons: list[ReasonName] | None = Field(
        default=None, max_length=5, description="Omit to keep the earlier chips."
    )
    comment: str | None = Field(
        default=None, max_length=1000, description="Omit to keep the earlier comment."
    )
