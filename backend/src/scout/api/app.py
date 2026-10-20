"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from scout.adapters.observability.telemetry import configure_telemetry, instrument_app, tune_gc
from scout.api import dev_auth, events, health, identity, profile
from scout.api.errors import install_error_handlers
from scout.api.logging import configure_logging
from scout.api.middleware import RequestContextMiddleware
from scout.bootstrap import Container, build_container, close_container, start_container
from scout.settings import get_settings


def create_app(container: Container | None = None) -> FastAPI:
    """Build the app. Tests pass a prepared container; production builds one from settings."""
    configure_logging(json=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c = container or build_container(get_settings())
        app.state.container = c
        configure_telemetry(
            c.settings.otel_service_name or "scout-api", c.settings.otel_exporter_otlp_endpoint,
            c.settings.otel_traces_sampler_arg,
        )  # fmt: skip
        instrument_app(app, c.db)
        tune_gc()
        await start_container(c)
        try:
            yield
        finally:
            await close_container(c)

    app = FastAPI(title="Lattice Scout API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(container.settings if container else get_settings()).cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(identity.router)
    app.include_router(profile.router)
    if (container.settings if container else get_settings()).auth_mode == "dev":
        app.include_router(dev_auth.router)
        structlog.get_logger().warning(
            "dev_login_enabled", note="POST /api/v1/dev/login signs in anyone; never expose"
        )
    return app
