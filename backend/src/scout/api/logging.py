"""structlog configuration: JSON logs enriched with the request context and the OTel trace id."""

from __future__ import annotations

import logging
import traceback
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


class ScrubExceptions(logging.Filter):
    """Standard-library log records (uvicorn, Starlette) keep the traceback frames but not the exception message.

    Messages can quote user content, including private vault text (database errors quote row and JSON values).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace ``exc_info`` by the class name and the frames; never the message."""
        if record.exc_info and record.exc_info[1] is not None:
            exc_type, _, tb = record.exc_info
            frames = "".join(traceback.format_tb(tb))
            record.exc_text = f"{exc_type.__name__ if exc_type else 'Exception'} (message withheld)\n{frames}"
            record.exc_info = None
        return True


_PROVIDER_LOGGERS = ("llmrouter_free", "llmrouter_free.router", "llmrouter_free.store", "LiteLLM", "litellm")


class ScrubProviderText(ScrubExceptions):
    """Router and provider library records keep their route, deployment and outcome, never the provider's text.

    A provider error can echo the generated answer or the prompt (for example a JSON-validation 400 quoting
    ``failed_generation``). The router formats its warning as ``"<outcome> on route 'r' via d: <error text>"``: the
    text after the first colon and the arguments that fill it are dropped (ADR 016, privacy).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Cut the record's message at the first colon, with the arguments that belong to the cut part."""
        super().filter(record)
        if record.args:
            head = str(record.msg).split(":", 1)[0]
            record.msg = head
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = args[: head.count("%")]
        return True


def install_exception_scrubber() -> None:
    """Attach the scrubbers to the root logger, uvicorn's and the LLM router's loggers (idempotent)."""
    for name in ("", "uvicorn", "uvicorn.error", "starlette", "fastapi", *_PROVIDER_LOGGERS):
        logger = logging.getLogger(name)
        flt: type[ScrubExceptions] = ScrubProviderText if name in _PROVIDER_LOGGERS else ScrubExceptions
        if not any(type(f) is flt for f in logger.filters):
            logger.addFilter(flt())
        for handler in logger.handlers:
            if not any(isinstance(f, ScrubExceptions) for f in handler.filters):
                handler.addFilter(ScrubExceptions())


def configure_logging(*, json: bool = True, level: int = logging.INFO) -> None:
    """Configure structlog (idempotent)."""
    install_exception_scrubber()
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
