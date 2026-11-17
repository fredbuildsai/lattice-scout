"""DTOs of the matches endpoints. Only fields the viewer may see; never the other side's decision."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

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


ExternalLayerName = Literal["in_network", "in_field"]
_ALL_SCOPES: tuple[ExternalLayerName, ...] = ("in_network", "in_field")


class ExternalSuggestionOut(BaseModel):
    """A researcher who is not on Lattice Scout, from public OpenAlex data. Never contacted by the platform."""

    id: UUID
    openalex_id: str = Field(
        description="Short OpenAlex author id; the profile is https://openalex.org/<id>."
    )
    openalex_url: str
    name: str
    institution: str | None
    layer: ExternalLayerName = Field(
        description="in_network: co-author of your co-authors; in_field: outside your network, same topics."
    )
    vias: list[str] = Field(description="Your co-authors who link you (in_network only).")
    shared_topics: list[str]
    score: float
    topic_overlap: float
    formula: str
    h_index: int
    citations: int
    works_count: int
    recent_works: int = Field(description="Works in the current year and the two before.")
    state: Literal["active", "saved", "dismissed"]
    created_at: datetime


class ExternalListOut(BaseModel):
    """Active and saved external suggestions, in_network first, then best score."""

    items: list[ExternalSuggestionOut]


class ExternalRefreshIn(BaseModel):
    """Which scopes to search. Out of network and out of field come later."""

    scopes: list[ExternalLayerName] = Field(default_factory=lambda: list(_ALL_SCOPES), min_length=1)


class ExternalStateIn(BaseModel):
    """Save, dismiss, or restore (active) a suggestion."""

    state: Literal["active", "saved", "dismissed"]
