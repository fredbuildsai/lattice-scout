"""DTOs of the matches endpoints. Only fields the viewer may see; never the other side's decision."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from scout.matching.domain.reveal import AnonymousMatchView, RevealedMatchView

MatchCardOut = Annotated[AnonymousMatchView | RevealedMatchView, Field(discriminator="kind")]


class MatchListOut(BaseModel):
    """A page of matches. ``matching_enabled`` is false until the account consents."""

    matching_enabled: bool
    items: list[MatchCardOut]
    next_cursor: str | None


class DecisionIn(BaseModel):
    """Accept, decline or withdraw."""

    action: Literal["accept", "decline", "withdraw"]
    never_similar: bool = False
