"""``python -m scout.worker`` entry point."""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

from scout.adapters.observability.partitions import schedule_today
from scout.adapters.observability.telemetry import configure_telemetry, tune_gc
from scout.api.logging import configure_logging
from scout.bootstrap import build_container, close_container, start_container
from scout.settings import get_settings
from scout.worker.loop import build_handlers, run_forever


async def main() -> None:
    """Build the container, run the loop until SIGINT/SIGTERM, then flush and close."""
    configure_logging(json=True)
    s = get_settings()
    c = build_container(s)
    configure_telemetry(
        s.otel_service_name or "scout-worker", s.otel_exporter_otlp_endpoint, s.otel_traces_sampler_arg
    )
    await start_container(c)
    tune_gc()
    SQLAlchemyInstrumentor().instrument(engine=c.db.engine.sync_engine)
    queue = c.job_queue
    await schedule_today(queue, c.clock)
    await c.sweep_vault.ensure_scheduled()
    handlers = build_handlers(c)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    try:
        await run_forever(
            queue,
            handlers,
            worker_id,
            s.worker_poll_interval_ms / 1000,
            stop,
            max(s.job_lease_seconds / 3, 1.0),
        )
    finally:
        await close_container(c)


if __name__ == "__main__":
    asyncio.run(main())
