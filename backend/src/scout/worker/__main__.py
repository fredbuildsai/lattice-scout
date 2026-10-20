"""``python -m scout.worker`` entry point."""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from scout.adapters.observability.partitions import schedule_today
from scout.adapters.observability.telemetry import configure_telemetry, tune_gc
from scout.adapters.queue.postgres import PostgresJobQueue
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
    queue = PostgresJobQueue(c.db, c.clock, c.ids, lease_seconds=s.job_lease_seconds)
    await schedule_today(queue, c.clock)
    handlers = build_handlers(c.db, c.clock, queue)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    try:
        await run_forever(queue, handlers, worker_id, s.worker_poll_interval_ms / 1000, stop)
    finally:
        await close_container(c)


if __name__ == "__main__":
    asyncio.run(main())
