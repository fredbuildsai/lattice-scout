"""Exception messages (which can quote vault text) never reach exported spans or standard-library logs."""

import io
import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from scout.adapters.observability.telemetry import ScrubExceptions
from scout.api.logging import ScrubExceptions as LogScrub

CANARY = "CANARY-vault-text-3b7d"


def _provider() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(ScrubExceptions())  # same order as configure_telemetry: scrub first
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_recorded_exceptions_and_status_keep_only_the_type() -> None:
    provider, exporter = _provider()
    tracer = provider.get_tracer("t")
    with pytest.raises(ValueError, match=CANARY), tracer.start_as_current_span("db") as span:
        span.set_attribute("db.operation", "INSERT")
        raise ValueError(f"Failing row contains ({CANARY})")
    [span_out] = exporter.get_finished_spans()
    dump = repr([(e.name, dict(e.attributes or {})) for e in span_out.events]) + repr(span_out.status)
    assert CANARY not in dump and "ValueError" in dump
    assert span_out.attributes is not None and span_out.attributes["db.operation"] == "INSERT"
    assert any(e.name == "exception" for e in span_out.events)


def test_spans_without_errors_are_untouched() -> None:
    provider, exporter = _provider()
    with provider.get_tracer("t").start_as_current_span("ok") as span:
        span.add_event("checkpoint", {"k": "v"})
    [out] = exporter.get_finished_spans()
    assert (
        [e.name for e in out.events] == ["checkpoint"] and out.status.is_ok
    ) or out.status.status_code.name == "UNSET"


def test_standard_library_log_records_drop_the_exception_message() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(LogScrub())
    log = logging.getLogger("scout.test.scrub")
    log.propagate = False
    log.addHandler(handler)
    try:
        try:
            raise RuntimeError(f"upstream quoted {CANARY}")
        except RuntimeError:
            log.exception("Exception in ASGI application")
    finally:
        log.removeHandler(handler)
    out = stream.getvalue()
    assert CANARY not in out and "RuntimeError" in out and "message withheld" in out
