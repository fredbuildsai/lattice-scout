"""Request-context middleware: request id, session id, page → contextvars. Pure ASGI (no body buffering)."""

from __future__ import annotations

import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scout.shared.context import RequestContext, set_context

log = structlog.get_logger()


class RequestContextMiddleware:
    """Reads ``X-Request-Id`` / ``X-Session-Id`` / ``X-Page``, sets the context, echoes the request id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        request_id = headers.get("x-request-id") or uuid4().hex
        set_context(
            RequestContext(
                request_id=request_id, session_id=headers.get("x-session-id"), page=headers.get("x-page")
            )
        )
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "request", method=scope["method"], path=scope["path"], status=status,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )  # fmt: skip
