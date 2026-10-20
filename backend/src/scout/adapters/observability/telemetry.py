"""OpenTelemetry set-up. A tracer provider is always installed (trace ids appear in logs and ``llm_call``);
OTLP export happens only when an endpoint is configured."""

from __future__ import annotations

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from scout.adapters.persistence.db import Database

_configured = False


def configure_telemetry(service_name: str, endpoint: str | None, sampler_ratio: float) -> None:
    """Install the global tracer provider once per process (idempotent)."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=ParentBased(TraceIdRatioBased(sampler_ratio)),
    )
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
        )
    trace.set_tracer_provider(provider)
    HTTPXClientInstrumentor().instrument()
    _configured = True


def instrument_app(app: FastAPI, db: Database) -> None:
    """Instrument FastAPI (health probes excluded) and the SQLAlchemy engine."""
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready")
    SQLAlchemyInstrumentor().instrument(engine=db.engine.sync_engine)
