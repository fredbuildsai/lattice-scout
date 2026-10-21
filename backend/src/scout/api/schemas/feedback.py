"""DTOs of the feedback endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReasonName = Literal["spot_on", "already_knew", "not_feasible", "wrong_field", "too_vague"]


class FeedbackIn(BaseModel):
    """A rating. Rating the same target again replaces the earlier one."""

    target_type: Literal["opportunity", "match"]
    target_id: UUID
    rating: Literal["up", "down"]
    reasons: list[ReasonName] = Field(default_factory=list, max_length=5)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    """The stored rating."""

    target_type: Literal["opportunity", "match"]
    target_id: UUID
    rating: Literal["up", "down"]
    reasons: list[ReasonName]
    comment: str | None
    updated_at: datetime
