"""Job loop: claim → run handler → complete / fail with back-off."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

import structlog
from opentelemetry import trace
from pydantic import JsonValue

from scout.adapters.observability import partitions
from scout.bootstrap import Container
from scout.ports.jobs import Job, JobQueuePort, retry_delay
from scout.shared import describe_exception
from scout.shared.context import RequestContext, set_context

log = structlog.get_logger()

Handler = Callable[[Job], Awaitable[dict[str, JsonValue]]]


async def ping(job: Job) -> dict[str, JsonValue]:
    """No-op job used to verify the queue end to end."""
    return {"pong": True}


DEFAULT_HANDLERS: Mapping[str, Handler] = {"ping": ping}


def _context_from(job: Job) -> RequestContext:
    """Background work inherits account/session/feature from the originating request (job payload)."""
    p = job.payload
    acct, run = p.get("account_id"), p.get("run_id")
    return RequestContext(
        request_id=f"job-{job.id}",
        session_id=str(p["session_id"]) if p.get("session_id") else None,
        account_id=UUID(str(acct)) if acct else None,
        feature=str(p["feature"]) if p.get("feature") else None,
        run_id=UUID(str(run)) if run else None,
    )


async def run_once(queue: JobQueuePort, handlers: Mapping[str, Handler], worker_id: str) -> bool:
    """Process at most one job. Returns True if a job was claimed."""
    job = await queue.claim(worker_id, list(handlers))
    if job is None:
        return False
    set_context(_context_from(job))
    try:
        with trace.get_tracer("scout.worker").start_as_current_span(f"job.{job.kind}") as span:
            span.set_attribute("job.id", str(job.id))
            span.set_attribute("job.attempts", job.attempts)
            result = await handlers[job.kind](job)
    except Exception as exc:
        safe = describe_exception(exc)
        log.warning("job_failed", job_id=str(job.id), kind=job.kind, attempt=job.attempts, error=safe)
        await queue.fail(job.id, safe, retry_delay(job.attempts))
    else:
        await queue.complete(job.id, result)
        log.info("job_done", job_id=str(job.id), kind=job.kind)
    return True


async def run_forever(
    queue: JobQueuePort,
    handlers: Mapping[str, Handler],
    worker_id: str,
    poll_interval: float,
    stop: asyncio.Event,
) -> None:
    """Poll until ``stop`` is set."""
    while not stop.is_set():
        if not await run_once(queue, handlers, worker_id):
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                pass


def build_handlers(c: Container) -> dict[str, Handler]:
    """All job handlers, closed over the container."""

    async def profile_import(job: Job) -> dict[str, JsonValue]:
        return await c.import_profile.handle(job.payload, last_attempt=job.attempts >= job.max_attempts)

    return {
        **DEFAULT_HANDLERS,
        partitions.KIND: partitions.make_handler(c.db, c.clock, c.job_queue),
        "profile.import": profile_import,
    }
