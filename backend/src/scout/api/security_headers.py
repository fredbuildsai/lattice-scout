"""Security headers on every response. Pure ASGI (no body buffering)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The API serves JSON only, so the CSP forbids everything; the interactive docs are served only in dev.
API_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Resource-Policy": "same-site",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
}
HSTS = "max-age=63072000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Adds the headers above (and HSTS in production) unless the route already set them."""

    def __init__(self, app: ASGIApp, *, hsts: bool = False, relaxed_paths: tuple[str, ...] = ()) -> None:
        self.app, self._hsts, self._relaxed = app, hsts, relaxed_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        relaxed = scope["path"].startswith(self._relaxed) if self._relaxed else False

        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start":
                h = MutableHeaders(scope=message)
                for k, v in API_HEADERS.items():
                    if relaxed and k == "Content-Security-Policy":
                        continue
                    if k not in h:
                        h[k] = v
                if self._hsts:
                    h["Strict-Transport-Security"] = HSTS
            await send(message)

        await self.app(scope, receive, wrapped)
