# M0 — `POST /api/v1/events` load test

Run on the cloud VM (4 vCPU), one uvicorn worker, local PostgreSQL 16, client on the same VM.
Command: `scripts/loadtest_events.py --batches 2500 --rps 100` (20 events per batch = 2 000 events/s,
plus `/health` and `/ready` at 50 rps each). Warm-up excluded. Three alternating runs per setting; values are ms.

| Setting | events p50 / p95 / p99 | /health p50 / p95 / p99 | /ready p95 |
|---|---|---|---|
| analytics ON (3 runs) | 3.4–3.5 / **5.8–6.5** / 21–26 | 4.0–4.2 / 6.2–7.3 / 23–34 | 12.3–13.6 |
| analytics OFF (3 runs) | 3.2–3.4 / 5.1–5.3 / 6.6–8.3 | 3.9–4.1 / 5.3–5.5 / 7.1–9.1 | 8.8–9.6 |

**Acceptance**
- `POST /events` p95 < 20 ms: **met** (about 6 ms at 2 000 events/s).
- API p95 within ±5 % with analytics on vs off: **partly met**. p50 is within 3 %; `/health` p95 is about
  1 ms (≈ 17 %) higher and p99 about 15 ms higher while a batch is being written. The residual cost is the
  background flush competing for the GIL. Tracked in `docs/BACKLOG.md` (move the writer to its own process
  or use a pipe-based COPY worker if the pilot shows it matters).

**What it took to get here** (first attempt: events p95 24–30 ms, /health p95 27–35 ms, loop stalls of 140 ms):
1. Writers run in a worker thread with a blocking engine (`asyncio.to_thread`), not on the event loop.
2. Analytics events are bulk-loaded with `COPY` instead of an SQLAlchemy multi-row `INSERT`.
3. `gc.freeze()` after start-up and `gc.set_threshold(20000, 20, 50)`: buffered objects made the cyclic GC
   pause the loop for 100+ ms.
4. Bug found by the first integration test: cancelling the flush loop in `close()` dropped the in-flight
   batch; `close()` now lets it finish (regression test `test_close_during_inflight_flush_loses_nothing`).
