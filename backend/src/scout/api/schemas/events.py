"""DTOs for ``POST /api/v1/events``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SessionMeta(BaseModel):
    """Client session description (sent with every batch)."""

    id: UUID = Field(description="Client-generated session UUID (rotates after 30 min of inactivity).")
    viewport: str | None = Field(default=None, max_length=16, examples=["1440x900"])
    app_version: str | None = Field(default=None, max_length=32)
    entry_page: str | None = Field(default=None, max_length=128)
    referrer_domain: str | None = Field(default=None, max_length=253)


class ClientEvent(BaseModel):
    """One analytics event. Names and property keys must exist in the event catalogue."""

    name: str = Field(max_length=64, examples=["page_view"])
    page: str | None = Field(default=None, max_length=128, description="Route pattern, never raw ids.")
    prev_page: str | None = Field(default=None, max_length=128)
    dwell_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=16)
    client_ts: datetime | None = None
    seq: int | None = Field(default=None, ge=0)


class EventBatch(BaseModel):
    """Batch of 1..100 events."""

    session: SessionMeta
    events: list[ClientEvent] = Field(min_length=1, max_length=100)


class Rejection(BaseModel):
    """Why an event was not accepted."""

    index: int
    reason: str


class EventsAccepted(BaseModel):
    """Result of ingestion. Writing happens asynchronously."""

    accepted: int
    rejected: list[Rejection]
