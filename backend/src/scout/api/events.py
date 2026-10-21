"""Analytics ingestion endpoint. Returns 202 immediately; persistence is asynchronous and buffered."""

from __future__ import annotations

import time
from datetime import UTC
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from scout.adapters.observability.user_agent import parse_user_agent
from scout.api.schemas.events import EventBatch, EventsAccepted, Rejection
from scout.bootstrap import Container
from scout.ports.analytics import AnalyticsEvent, SessionTouch
from scout.ports.analytics_catalogue import validate_event, validate_page
from scout.shared import AccountId, ValidationFailed
from scout.shared.context import get_context

router = APIRouter(prefix="/api/v1", tags=["analytics"])

MAX_BODY_BYTES = 64 * 1024
_OPT_OUT_TTL_S = 60.0
_opt_out_cache: dict[UUID, tuple[float, bool]] = {}


async def _bearer_account(request: Request, c: Container) -> UUID | None:
    """The signed-in account, if the request carries a valid token (events work without one)."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    try:
        return (await c.auth_verifier.verify(token.strip())).sub
    except Exception:
        return None


def forget_opt_out(account_id: UUID) -> None:
    """Drop the cached opt-out flag (called when the preference changes)."""
    _opt_out_cache.pop(account_id, None)


async def _opted_out(c: Container, account_id: UUID) -> bool:
    """Whether the account switched analytics off; cached for a minute so ingestion stays cheap."""
    hit = _opt_out_cache.get(account_id)
    now = time.monotonic()
    if hit and now - hit[0] < _OPT_OUT_TTL_S:
        return hit[1]
    try:
        flag = (await c.notifications.get_prefs(AccountId(account_id))).analytics_opt_out
    except Exception:
        return False  # observability never blocks a request
    if len(_opt_out_cache) > 10_000:
        _opt_out_cache.clear()
    _opt_out_cache[account_id] = (now, flag)
    return flag


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
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise ValidationFailed("Event batch too large", extra={"max_bytes": MAX_BODY_BYTES})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ValidationFailed("Event batch too large", extra={"max_bytes": MAX_BODY_BYTES})
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        batch = EventBatch.model_validate_json(body)
    except ValidationError as exc:
        raise ValidationFailed("Invalid event batch", extra={"errors": exc.error_count()}) from exc

    c: Container = request.app.state.container
    ctx = get_context()
    now = c.clock.now()
    account_id = ctx.account_id or await _bearer_account(request, c)
    if account_id is not None and await _opted_out(c, account_id):
        return EventsAccepted(accepted=0, rejected=[])  # the user opted out: nothing is stored
    rejected: list[Rejection] = []
    if validate_page(batch.session.entry_page):
        batch.session.entry_page = None
    accepted = 0
    for i, e in enumerate(batch.events):
        reason = validate_event(e.name, e.properties) or validate_page(e.page) or validate_page(e.prev_page)
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
