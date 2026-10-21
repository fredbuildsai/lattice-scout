"""DTOs for ``POST /api/v1/events``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SessionMeta(BaseModel):
    """Client session description (sent with every batch)."""

    id: UUID = Field(description="Client-generated session UUID (rotates after 30 min of inactivity).")
    viewport: str | None = Field(
        default=None, max_length=16, examples=["1440x900"], description="Window size WxH."
    )
    app_version: str | None = Field(default=None, max_length=32, description="Frontend build version.")
    entry_page: str | None = Field(
        default=None, max_length=128, description="Route pattern of the first page of the session."
    )
    referrer_domain: str | None = Field(
        default=None, max_length=253, description="Domain of the referrer, never a full URL."
    )


class ClientEvent(BaseModel):
    """One analytics event. Names and property keys must exist in the event catalogue."""

    name: str = Field(max_length=64, examples=["page_view"], description="Event name from the catalogue.")
    page: str | None = Field(default=None, max_length=128, description="Route pattern, never raw ids.")
    prev_page: str | None = Field(
        default=None, max_length=128, description="Route pattern of the page the user came from."
    )
    dwell_ms: int | None = Field(
        default=None, ge=0, le=86_400_000, description="Milliseconds spent on the page being left."
    )
    properties: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        max_length=16,
        description="Scalar properties allowed for this event name (ids and enums only).",
    )
    client_ts: datetime | None = Field(default=None, description="Client clock when the event happened.")
    seq: int | None = Field(
        default=None, ge=0, description="Per-session counter; orders events within a batch."
    )


class EventBatch(BaseModel):
    """Batch of 1..100 events."""

    session: SessionMeta = Field(description="Client session this batch belongs to.")
    events: list[ClientEvent] = Field(min_length=1, max_length=100, description="Events, oldest first.")


class Rejection(BaseModel):
    """Why an event was not accepted."""

    index: int = Field(description="Position of the rejected event in the submitted batch.")
    reason: str = Field(
        description="Why the event was refused (unknown name or property, too long, not scalar)."
    )


class EventsAccepted(BaseModel):
    """Result of ingestion. Writing happens asynchronously."""

    accepted: int = Field(description="Number of events queued for storage.")
    rejected: list[Rejection] = Field(description="Events that failed catalogue validation.")
