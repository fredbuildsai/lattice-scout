"""``python -m scout.worker`` entry point."""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from scout.adapters.queue.postgres import PostgresJobQueue
from scout.api.logging import configure_logging
from scout.bootstrap import build_container, close_container, start_container
from scout.settings import get_settings
from scout.worker.loop import DEFAULT_HANDLERS, run_forever


async def main() -> None:
    """Build the container, run the loop until SIGINT/SIGTERM, then flush and close."""
    configure_logging(json=True)
    s = get_settings()
    c = build_container(s)
    await start_container(c)
    queue = PostgresJobQueue(c.db, c.clock, c.ids, lease_seconds=s.job_lease_seconds)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    try:
        await run_forever(queue, DEFAULT_HANDLERS, worker_id, s.worker_poll_interval_ms / 1000, stop)
    finally:
        await close_container(c)


if __name__ == "__main__":
    asyncio.run(main())
