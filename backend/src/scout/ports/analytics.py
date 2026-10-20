"""Product-analytics port. Buffered and non-blocking."""

from __future__ import annotations

from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

type Prop = str | int | float | bool | None


class AnalyticsEvent(BaseModel):
    """One enriched event (``analytics_event`` row)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    ts: AwareDatetime
    account_id: UUID | None = None
    session_id: UUID
    name: str
    page: str | None = None
    prev_page: str | None = None
    dwell_ms: int | None = None
    properties: dict[str, Prop] = {}
    client_ts: AwareDatetime | None = None
    seq: int | None = None


class SessionTouch(BaseModel):
    """Upsert of an ``app_session`` row."""

    model_config = ConfigDict(frozen=True)

    session_id: UUID
    account_id: UUID | None = None
    seen_at: AwareDatetime
    user_agent_family: str | None = None
    device_type: Literal["desktop", "mobile", "tablet", "other"] | None = None
    viewport: str | None = None
    app_version: str | None = None
    entry_page: str | None = None
    referrer_domain: str | None = None


class AnalyticsPort(Protocol):
    """Buffered analytics sink."""

    def track(self, evt: AnalyticsEvent) -> None:
        """Buffer one event (never raises)."""
        ...

    def touch_session(self, s: SessionTouch) -> None:
        """Buffer a session upsert (never raises)."""
        ...
