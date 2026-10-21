"""OpenTelemetry set-up. A tracer provider is always installed (trace ids appear in logs and ``llm_call``);
OTLP export happens only when an endpoint is configured."""

from __future__ import annotations

import gc

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.sdk.util import BoundedList
from opentelemetry.trace import Status, StatusCode

from scout.adapters.persistence.db import Database

_configured = False


class ScrubExceptions(SpanProcessor):
    """Remove exception messages and stack traces from finished spans before any exporter sees them.

    Instrumentation (SQLAlchemy, FastAPI, httpx) records exceptions with their message, and database messages
    quote row and JSON values, which can be private vault text. Only the exception type is kept.
    """

    def on_end(self, span: ReadableSpan) -> None:
        """Rewrite exception events and the status description in place."""
        events = getattr(span, "_events", None)
        if events is not None:
            clean = [
                Event(ev.name, {"exception.type": (ev.attributes or {}).get("exception.type", "Exception"),
                                "exception.message": "withheld"}, ev.timestamp)
                if ev.name == "exception" else ev
                for ev in events
            ]  # fmt: skip
            object.__setattr__(
                span,
                "_events",
                BoundedList.from_seq(getattr(getattr(events, "_dq", None), "maxlen", None), clean),
            )
        status = span.status
        if (
            status.status_code is StatusCode.ERROR
            and status.description
            and not status.description.startswith("type:")
        ):
            kind = status.description.split(":", 1)[0][:80]
            object.__setattr__(span, "_status", Status(StatusCode.ERROR, f"type: {kind}"))


def configure_telemetry(service_name: str, endpoint: str | None, sampler_ratio: float) -> None:
    """Install the global tracer provider once per process (idempotent)."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=ParentBased(TraceIdRatioBased(sampler_ratio)),
    )
    provider.add_span_processor(ScrubExceptions())  # first: runs before every exporter
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


def tune_gc() -> None:
    """Freeze start-up objects and raise the young-generation threshold.

    Buffered analytics keeps thousands of small objects alive between flushes; with default thresholds the cyclic
    GC then pauses the event loop for 100+ ms (measured in docs/perf/M0-events-loadtest.md).
    """
    gc.collect()
    gc.freeze()
    gc.set_threshold(20_000, 20, 50)
