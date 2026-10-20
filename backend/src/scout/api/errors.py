"""RFC 9457 problem+json error handlers."""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from scout.shared import ScoutError
from scout.shared.context import get_context

PROBLEM_JSON = "application/problem+json"
log = structlog.get_logger()


def _problem(request: Request, status: int, body: dict[str, object]) -> JSONResponse:
    body = {**body, "instance": request.url.path, "request_id": get_context().request_id}
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    """Register handlers so every error leaves as problem+json."""

    @app.exception_handler(ScoutError)
    async def _scout(request: Request, exc: ScoutError) -> JSONResponse:
        if exc.http_status >= 500:
            log.error("request_failed", code=exc.code, detail=exc.detail)
            body = {**exc.to_problem(), "detail": exc.title}
        else:
            body = exc.to_problem()
        return _problem(request, exc.http_status, body)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e["loc"]], "msg": e["msg"]} for e in exc.errors()]
        return _problem(
            request,
            422,
            {"type": "/problems/validation_failed", "title": "Validation failed", "status": 422,
             "code": "validation_failed", "detail": "Request validation failed", "errors": errors},
        )  # fmt: skip

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            exc.status_code,
            {"type": "/problems/http_error", "title": "HTTP error", "status": exc.status_code,
             "code": "http_error", "detail": str(exc.detail)},
        )  # fmt: skip

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_exception", exc_info=exc)
        return _problem(
            request,
            500,
            {"type": "/problems/internal_error", "title": "Internal error", "status": 500,
             "code": "internal_error", "detail": "Internal error"},
        )  # fmt: skip
