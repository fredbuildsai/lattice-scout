"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from scout.adapters.observability.telemetry import configure_telemetry, instrument_app
from scout.api import events, health
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
        await start_container(c)
        try:
            yield
        finally:
            await close_container(c)

    app = FastAPI(title="Lattice Scout API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(events.router)
    return app
