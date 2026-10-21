"""Error hierarchy. Each class carries hints the API layer maps to RFC 9457 problem+json."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

Extra = Mapping[str, str | int | bool | None]


class ScoutError(Exception):
    """Base class of all application errors."""

    code: ClassVar[str] = "internal_error"
    http_status: ClassVar[int] = 500
    title: ClassVar[str] = "Internal error"

    def __init__(self, detail: str = "", *, extra: Extra | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra: dict[str, str | int | bool | None] = dict(extra or {})

    def to_problem(self) -> dict[str, object]:
        """Return the problem+json body (without ``instance`` / ``request_id``)."""
        return {
            "type": f"/problems/{self.code}",
            "title": self.title,
            "status": self.http_status,
            "detail": self.detail,
            "code": self.code,
            **self.extra,
        }


class DomainError(ScoutError):
    """A business rule was not respected."""

    code = "domain_error"
    http_status = 400
    title = "Domain error"


class NotFound(DomainError):
    """The resource does not exist, or the caller may not know it exists."""

    code = "not_found"
    http_status = 404
    title = "Not found"


class Conflict(DomainError):
    """The request conflicts with the current state."""

    code = "conflict"
    http_status = 409
    title = "Conflict"


class InvalidStateTransition(Conflict):
    """A state machine refused the transition."""

    code = "invalid_state_transition"
    title = "Invalid state transition"


class InvariantViolation(DomainError):
    """A domain invariant would be broken."""

    code = "invariant_violation"
    http_status = 422
    title = "Invariant violation"


class ValidationFailed(DomainError):
    """Input is malformed."""

    code = "validation_failed"
    http_status = 422
    title = "Validation failed"


class Unauthenticated(DomainError):
    """No valid credentials."""

    code = "unauthenticated"
    http_status = 401
    title = "Authentication required"


class Forbidden(DomainError):
    """Authenticated but not allowed (use ``NotFound`` when existence must stay private)."""

    code = "forbidden"
    http_status = 403
    title = "Forbidden"


class RateLimited(DomainError):
    """A rate limit or budget was reached."""

    code = "rate_limited"
    http_status = 429
    title = "Too many requests"


class InfrastructureError(ScoutError):
    """An external dependency (database, HTTP API) failed."""

    code = "upstream_unavailable"
    http_status = 503
    title = "Service unavailable"


def describe_exception(exc: BaseException, limit: int = 200) -> str:
    """Log-safe description: class name plus a truncated message, never user-supplied input values.

    Pydantic validation errors embed the offending input (possibly vault text), so only their locations and
    error types are kept.
    """
    orig = getattr(
        exc, "orig", None
    )  # SQLAlchemy DBAPIError: the driver message quotes row values and JSON text
    if orig is not None:
        state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
        parts = [
            type(orig).__name__,
            f"sqlstate={state}" if state else "",
            f"constraint={constraint}" if constraint else "",
        ]
        return f"{type(exc).__name__}: {' '.join(p for p in parts if p)}"[:limit]
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            parts = [f"{'.'.join(str(x) for x in e.get('loc', ()))}:{e.get('type', '?')}" for e in errors()]
            return f"{type(exc).__name__}: {'; '.join(parts)}"[:limit]
        except Exception:  # noqa: S110
            pass
    return f"{type(exc).__name__}: {exc}"[:limit]
