"""Job loop: claim → run handler → complete / fail with back-off."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from uuid import UUID

import structlog
from opentelemetry import trace
from pydantic import JsonValue
from sqlalchemy import text

from scout.adapters.observability import partitions
from scout.bootstrap import Container
from scout.ports.jobs import Job, JobQueuePort, JobSpec, retry_delay
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


async def _beat(queue: JobQueuePort, job_id: UUID, worker_id: str, every: float) -> None:
    """Keep the lease of a long-running job alive until cancelled."""
    while True:
        await asyncio.sleep(every)
        try:
            if not await queue.heartbeat(job_id, worker_id):
                log.warning("job_lease_lost", job_id=str(job_id))  # another worker owns the job now
        except Exception:  # a missed beat is retried; the lease still has slack
            log.warning("job_heartbeat_failed", job_id=str(job_id))


async def run_once(
    queue: JobQueuePort, handlers: Mapping[str, Handler], worker_id: str, heartbeat_s: float = 60.0
) -> bool:
    """Process at most one job. Returns True if a job was claimed.

    The lease is extended every ``heartbeat_s`` seconds while the handler runs, so a long job (many LLM calls) is
    not claimed by a second worker.
    """
    job = await queue.claim(worker_id, list(handlers))
    if job is None:
        return False
    set_context(_context_from(job))
    with trace.get_tracer("scout.worker").start_as_current_span(
        f"job.{job.kind}", record_exception=False, set_status_on_exception=False
    ) as span:
        span.set_attribute("job.id", str(job.id))
        span.set_attribute("job.attempts", job.attempts)
        beat = asyncio.create_task(_beat(queue, job.id, worker_id, heartbeat_s))
        try:
            result = await handlers[job.kind](job)
        except Exception as exc:
            safe = describe_exception(exc)
            span.set_status(
                trace.Status(trace.StatusCode.ERROR, safe)
            )  # never the raw message (may quote vault text)
            log.warning("job_failed", job_id=str(job.id), kind=job.kind, attempt=job.attempts, error=safe)
            await queue.fail(job.id, safe, retry_delay(job.attempts), worker_id)
        else:
            await queue.complete(job.id, result, worker_id)
            log.info("job_done", job_id=str(job.id), kind=job.kind)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)  # never leave the cancelled task running
    return True


async def run_forever(
    queue: JobQueuePort,
    handlers: Mapping[str, Handler],
    worker_id: str,
    poll_interval: float,
    stop: asyncio.Event,
    heartbeat_s: float = 60.0,
) -> None:
    """Poll until ``stop`` is set."""
    while not stop.is_set():
        if not await run_once(queue, handlers, worker_id, heartbeat_s):
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_interval)
            except TimeoutError:
                pass


def build_handlers(c: Container) -> dict[str, Handler]:
    """All job handlers, closed over the container."""

    async def profile_import(job: Job) -> dict[str, JsonValue]:
        result = await c.import_profile.handle(job.payload, last_attempt=job.attempts >= job.max_attempts)
        async with c.db.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT account_id FROM researcher WHERE id = :r"),
                    {"r": str(job.payload["researcher_id"])},
                )
            ).first()
        account = str(row.account_id) if row else None
        if account and result.get("skipped") is None:
            await c.job_queue.enqueue(
                JobSpec(
                    kind="matching.profile",
                    payload={"account_id": account},
                    dedupe_key=f"profile:{account}",
                    max_attempts=3,
                )
            )
        return result

    async def matching_profile(job: Job) -> dict[str, JsonValue]:
        return await c.match_profile.handle(job.payload, last_attempt=last(job))

    async def matching_compute(job: Job) -> dict[str, JsonValue]:
        return await c.compute_matches.handle(job.payload, last_attempt=last(job))

    async def matching_reveal(job: Job) -> dict[str, JsonValue]:
        return await c.reveal_match.handle(job.payload, last_attempt=last(job))

    async def matching_nightly(job: Job) -> dict[str, JsonValue]:
        return await c.match_nightly.handle(job.payload, last_attempt=last(job))

    async def account_delete(job: Job) -> dict[str, JsonValue]:
        return await c.delete_account.handle(job.id, job.payload, last_attempt=last(job))

    async def identity_sweep(job: Job) -> dict[str, JsonValue]:
        return await c.sweep_erasures.handle(job.payload, last_attempt=last(job))

    async def notification_send(job: Job) -> dict[str, JsonValue]:
        return await c.notifications.deliver(job.payload)

    async def notification_digest(job: Job) -> dict[str, JsonValue]:
        return await c.weekly_digest.handle(job.payload, last_attempt=last(job))

    def last(job: Job) -> bool:
        return job.attempts >= job.max_attempts

    async def vault_ingest(job: Job) -> dict[str, JsonValue]:
        return await c.ingest_document.handle(job.payload, last_attempt=last(job))

    async def vault_delete(job: Job) -> dict[str, JsonValue]:
        return await c.delete_document_job.handle(job.payload, last_attempt=last(job))

    async def vault_sweep(job: Job) -> dict[str, JsonValue]:
        return await c.sweep_vault.handle(job.payload, last_attempt=last(job))

    async def discovery_run(job: Job) -> dict[str, JsonValue]:
        return await c.run_analysis.handle(job.payload, last_attempt=last(job))

    async def discovery_sweep(job: Job) -> dict[str, JsonValue]:
        return await c.sweep_runs.handle(job.payload, last_attempt=last(job))

    return {
        **DEFAULT_HANDLERS,
        partitions.KIND: partitions.make_handler(c.db, c.clock, c.job_queue),
        "profile.import": profile_import,
        "vault.ingest": vault_ingest,
        "vault.delete": vault_delete,
        "vault.sweep": vault_sweep,
        "discovery.run": discovery_run,
        "discovery.sweep": discovery_sweep,
        "matching.profile": matching_profile,
        "matching.compute": matching_compute,
        "matching.reveal": matching_reveal,
        "matching.nightly": matching_nightly,
        "account.delete": account_delete,
        "notification.send": notification_send,
        "notification.digest": notification_digest,
        "identity.sweep": identity_sweep,
    }
