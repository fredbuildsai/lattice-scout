"""Pure-ASGI guard for ``POST /api/v1/documents``.

FastAPI parses multipart bodies *before* it resolves dependencies (authentication) and Starlette spools file parts
to disk without a limit. This guard answers 401 and 413 before the body is touched and aborts streamed bodies
(chunked uploads have no ``Content-Length``) as soon as they pass the limit.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scout.identity.ports.auth import InvalidToken
from scout.shared import ScoutError, Unauthenticated
from scout.vault.domain.upload import FileTooLarge

ENVELOPE = 64 * 1024  # multipart framing allowed on top of the file limit


class _TooLarge(Exception):
    pass


async def _problem(send: Send, exc: ScoutError) -> None:
    body = json.dumps(exc.to_problem()).encode()
    await send(
        {
            "type": "http.response.start", "status": exc.http_status,
            "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode())],
        }
    )  # fmt: skip
    await send({"type": "http.response.body", "body": body})


class UploadGuard:
    """Refuse unauthenticated and oversized uploads without reading the body."""

    def __init__(self, app: ASGIApp, path: str, max_file_bytes: int) -> None:
        self._app, self._path, self._max = app, path, max_file_bytes + ENVELOPE

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != self._path:
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        scheme, _, token = headers.get(b"authorization", b"").decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            await _problem(send, Unauthenticated())
            return
        try:  # a forged or expired token must not make us read 25 MB either
            await scope["app"].state.container.auth_verifier.verify(token.strip())
        except InvalidToken:
            await _problem(send, Unauthenticated())
            return
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self._max:
            await _problem(send, FileTooLarge("The file is larger than the limit"))
            return
        seen = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._max:
                    raise _TooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self._app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await _problem(send, FileTooLarge("The file is larger than the limit"))
