# worker — background job runner

`python -m scout.worker` (or `make worker`) runs one process that claims jobs from the PostgreSQL `job` table,
runs the matching handler and records the result. Long-running or retryable work leaves the request path
through this queue (ADR-006).

## Modules

| Module | Role |
|---|---|
| `__main__.py` | Entry point. Builds the container from settings, starts it, schedules today's partition job, installs SIGINT/SIGTERM handlers, runs the loop, then closes the container (flushes buffered writers). |
| `loop.py` | `run_once` (claim, run, complete or fail), `run_forever` (poll until stopped), `build_handlers` (handler registry), `DEFAULT_HANDLERS` (`ping`). |

## Loop

1. `run_forever` calls `run_once` until the stop event is set.
2. `run_once` claims the next due job for the kinds that have a handler. The claim uses `SELECT ... FOR UPDATE SKIP LOCKED`
   and takes a lease of `JOB_LEASE_SECONDS`. A job whose lease expired can be claimed again.
3. The request context is rebuilt from the job payload (`account_id`, `session_id`, `feature`, `run_id`), so logs
   and `llm_call` rows written by the job carry the same ids as the request that enqueued it.
4. The handler runs inside an OpenTelemetry span named `job.<kind>`.
5. Success: `queue.complete(job.id, result)`. Failure: `queue.fail(job.id, error, retry_delay(attempts))`.
6. When no job is due, the loop waits `WORKER_POLL_INTERVAL_MS` (or stops early on a signal).

Retry back-off is `retry_delay(attempt)` from `scout.ports.jobs`: 5, 10, 20 … seconds, capped at 600. A job is
marked `dead` once its attempts reach `max_attempts` (default 5).

Job statuses: `queued`, `running`, `succeeded`, `dead`.

## Handlers

| Kind | Handler | What it does |
|---|---|---|
| `ping` | `loop.ping` | Returns `{"pong": true}`. Used to check the queue end to end. |
| `maintenance.ensure_partitions` | `partitions.make_handler` | Calls `public.scout_ensure_month_partition` for `analytics_event`, `llm_call` and `external_call`, for the current month and the next 3. Then it enqueues itself for 00:05 UTC the next day, deduplicated by date. Returns `{"ensured": n}`. |

At start-up the worker enqueues one partition job for today. The dedupe key stops restarts from piling up jobs.

## Ports and adapters

- Port: `JobQueuePort` in `scout/ports/jobs.py` (`enqueue`, `claim`, `complete`, `fail`).
- Adapter: `PostgresJobQueue` in `scout/adapters/queue/postgres.py`.
- Test double: the in-memory queue in `backend/tests/fakes/jobs.py`.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_POLL_INTERVAL_MS` | `1000` | Delay between polls when the queue is empty. |
| `JOB_LEASE_SECONDS` | `300` | How long a claimed job is locked before another worker may take it. |

## Testing

- `backend/tests/unit/test_worker_loop.py`: `ping` completes; a failing job is retried with back-off, then dead (in-memory queue).
- `backend/tests/contract/test_job_queue.py`: the same queue contract (claim once, retry and dead, lease expiry, dedupe,
  no double claim under concurrency) runs against the in-memory queue and PostgreSQL.
- `backend/tests/integration/test_worker_postgres.py`: `ping` and the partition job against PostgreSQL (`make test-integration`).

Run: `cd backend && uv run pytest tests/unit/test_worker_loop.py -q`.
