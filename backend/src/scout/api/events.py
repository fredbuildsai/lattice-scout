"""Analytics ingestion endpoint. Returns 202 immediately; persistence is asynchronous and buffered."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Request
from pydantic import ValidationError

from scout.adapters.observability.user_agent import parse_user_agent
from scout.api.schemas.events import EventBatch, EventsAccepted, Rejection
from scout.bootstrap import Container
from scout.ports.analytics import AnalyticsEvent, SessionTouch
from scout.ports.analytics_catalogue import validate_event
from scout.shared import ValidationFailed
from scout.shared.context import get_context

router = APIRouter(prefix="/api/v1", tags=["analytics"])

MAX_BODY_BYTES = 64 * 1024


@router.post(
    "/events",
    status_code=202,
    summary="Ingest a batch of product-analytics events",
    description=(
        "Accepts up to 100 events. Accepts `application/json` or `text/plain` containing JSON "
        "(navigator.sendBeacon cannot set headers). Unknown event names or property keys are rejected "
        "per event. Returns 202 without waiting for the database."
    ),
    response_model=EventsAccepted,
)
async def post_events(request: Request) -> EventsAccepted:
    """Validate, enrich and buffer events."""
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ValidationFailed("Event batch too large", extra={"max_bytes": MAX_BODY_BYTES})
    try:
        batch = EventBatch.model_validate_json(body)
    except ValidationError as exc:
        raise ValidationFailed("Invalid event batch", extra={"errors": exc.error_count()}) from exc

    c: Container = request.app.state.container
    ctx = get_context()
    now = c.clock.now()
    rejected: list[Rejection] = []
    accepted = 0
    for i, e in enumerate(batch.events):
        reason = validate_event(e.name, e.properties)
        if reason:
            rejected.append(Rejection(index=i, reason=reason))
            continue
        c.analytics.track(
            AnalyticsEvent(
                id=c.ids.new(),
                ts=now,
                account_id=ctx.account_id,
                session_id=batch.session.id,
                name=e.name,
                page=e.page,
                prev_page=e.prev_page,
                dwell_ms=e.dwell_ms,
                properties=e.properties,
                client_ts=e.client_ts.astimezone(UTC) if e.client_ts and e.client_ts.tzinfo else None,
                seq=e.seq,
            )  # fmt: skip
        )
        accepted += 1
    family, device = parse_user_agent(request.headers.get("user-agent"))
    c.analytics.touch_session(
        SessionTouch(
            session_id=batch.session.id,
            account_id=ctx.account_id,
            seen_at=now,
            user_agent_family=family,
            device_type=device,
            viewport=batch.session.viewport,
            app_version=batch.session.app_version,
            entry_page=batch.session.entry_page,
            referrer_domain=batch.session.referrer_domain,
        )  # fmt: skip
    )
    return EventsAccepted(accepted=accepted, rejected=rejected)
