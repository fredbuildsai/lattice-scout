"""Request-context middleware: request id, session id, page → contextvars. Pure ASGI (no body buffering)."""

from __future__ import annotations

import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scout.shared.context import RequestContext, set_context

log = structlog.get_logger()
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


class RequestContextMiddleware:
    """Reads ``X-Request-Id`` / ``X-Session-Id`` / ``X-Page``, sets the context, echoes the request id."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        raw_id = headers.get("x-request-id", "")
        request_id = raw_id if _SAFE_ID.match(raw_id) else uuid4().hex
        raw_session = headers.get("x-session-id", "")
        session_id = raw_session if _UUID.match(raw_session) else None
        page = headers.get("x-page", "")[:128] or None
        set_context(RequestContext(request_id=request_id, session_id=session_id, page=page))
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
