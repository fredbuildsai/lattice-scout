"""Security surface (M6 slice E): response headers, CORS, production refusals, and an auth guard on every route."""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

from scout.api.app import create_app
from scout.api.deps import current_account, current_principal
from scout.settings import Settings

# Routes that are public on purpose.
PUBLIC = {
    ("GET", "/health"), ("GET", "/ready"), ("POST", "/api/v1/events"), ("POST", "/api/v1/dev/login"),
}  # fmt: skip


def _routes() -> list[APIRoute]:
    """Every API route; FastAPI wraps included routers, so look inside them."""
    out: list[APIRoute] = []
    for r in create_app().routes:
        inner = getattr(r, "original_router", None)
        out += [x for x in (inner.routes if inner else [r]) if isinstance(x, APIRoute)]
    return out


def _deps(route: APIRoute) -> set[object]:
    out: set[object] = set()
    stack = [route.dependant]
    while stack:
        d = stack.pop()
        if d.call is not None:
            out.add(d.call)
        stack.extend(d.dependencies)
    return out


def test_the_route_walk_finds_the_api() -> None:
    paths = {r.path for r in _routes()}
    assert len(paths) > 20 and "/api/v1/me" in paths and "/api/v1/me/delete" in paths


def test_every_route_requires_an_account_unless_listed_public() -> None:
    open_routes = []
    for r in _routes():
        deps = _deps(r)
        guarded = (
            current_account in deps
            or current_principal in deps
            or any(getattr(c, "__name__", "").startswith(("current_", "require_")) for c in deps)
        )
        if not guarded:
            open_routes += [(m, r.path) for m in r.methods or ()]
    assert sorted(set(open_routes) - PUBLIC) == []


def test_api_responses_carry_security_headers() -> None:
    with TestClient(create_app()) as c:
        r = c.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" not in r.headers  # dev


def test_cors_allows_only_listed_origin_methods_and_headers() -> None:
    with TestClient(create_app()) as c:
        ok = c.options(
            "/api/v1/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "PATCH",
                "Access-Control-Request-Headers": "authorization,x-session-id",
            },
        )
        bad_origin = c.options(
            "/api/v1/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
        )
        bad_header = c.options(
            "/api/v1/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-evil",
            },
        )
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in bad_origin.headers
    assert bad_header.status_code == 400


@pytest.mark.parametrize("origins", [["*"], ["http://app.example.edu"]])
def test_production_refuses_wildcard_and_plain_http_origins(origins: list[str]) -> None:
    kw = {
        "llm_backend": "openai_direct", "openai_api_key": "k", "literature_backend": "openalex",
        "email_provider": "resend", "email_api_key": "k", "email_from": "a@b.c", "storage_backend": "supabase",
        "supabase_url": "https://x.supabase.co", "supabase_service_role_key": "k", "auth_mode": "supabase",
        "audit_hash_key": "k" * 32,
    }  # fmt: skip
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(_env_file=None, app_env="prod", cors_origins=origins, **kw)  # type: ignore[arg-type]


def test_agent_traces_never_include_prompt_or_response_text() -> None:
    """The Agents SDK sends prompts and responses to its trace exporter unless told not to (static guard)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "scout"
    text = "\n".join(p.read_text() for p in src.rglob("*.py"))
    assert "trace_include_sensitive_data=True" not in text
    assert "trace_include_sensitive_data=False" in (src / "adapters/llm/openai_direct.py").read_text()
    assert "set_tracing_export_api_key" not in text
