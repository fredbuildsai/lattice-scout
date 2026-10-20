"""Per-request context propagated through contextvars (logs, traces, usage metering, analytics)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Who/where a unit of work belongs to. Background jobs rebuild it from their payload."""

    request_id: str = ""
    session_id: str | None = None
    page: str | None = None
    account_id: UUID | None = None
    feature: str | None = None
    run_id: UUID | None = None


_current: ContextVar[RequestContext] = ContextVar("scout_request_context", default=RequestContext())  # noqa: B039 (frozen)


def get_context() -> RequestContext:
    """Return the current context (an empty one outside a request)."""
    return _current.get()


def set_context(ctx: RequestContext) -> None:
    """Replace the current context."""
    _current.set(ctx)


def update_context(**changes: object) -> RequestContext:
    """Replace fields on the current context and return the result."""
    ctx = replace(_current.get(), **changes)  # type: ignore[arg-type]
    _current.set(ctx)
    return ctx
