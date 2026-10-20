"""structlog configuration: JSON logs enriched with the request context and the OTel trace id."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog
from opentelemetry import trace

from scout.shared.context import get_context


def _add_context(_: Any, __: str, event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    ctx = get_context()
    event.setdefault("request_id", ctx.request_id)
    if ctx.session_id:
        event.setdefault("session_id", ctx.session_id)
    if ctx.account_id:
        event.setdefault("account_id", str(ctx.account_id))
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        event.setdefault("trace_id", format(span.trace_id, "032x"))
    return event


def configure_logging(*, json: bool = True, level: int = logging.INFO) -> None:
    """Configure structlog (idempotent)."""
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_context,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=False,
    )
