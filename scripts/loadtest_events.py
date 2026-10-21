#!/usr/bin/env python3
"""Load test for POST /api/v1/events and /health,/ready (acceptance: events p95 < 20 ms; health/ready p95
within 5 % with analytics on vs off).

Usage: python scripts/loadtest_events.py --base http://localhost:8000 [--batches 3000] [--concurrency 20]
Start the API first (one worker) with ANALYTICS_ENABLED=true/false, run this once per setting, compare p95s.
Needs httpx (installed in backend/.venv).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from uuid import uuid4

import httpx

PAGES = ["/home", "/vault", "/runs", "/opportunities", "/matches"]


def batch(n: int = 20) -> dict[str, object]:
    return {
        "session": {"id": str(uuid4()), "viewport": "1440x900"},
        "events": [
            {"name": "page_view", "page": PAGES[i % 5], "prev_page": PAGES[(i - 1) % 5], "dwell_ms": 100, "seq": i}
            for i in range(n)
        ],
    }


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--batches", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--warmup", type=int, default=200)
    ap.add_argument("--rps", type=float, default=100, help="events batches per second (open loop)")
    a = ap.parse_args()
    lat: dict[str, list[float]] = {"events": [], "health": [], "ready": []}
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(base_url=a.base, timeout=10) as client:

        async def one(kind: str, i: int) -> None:
            async with sem:
                t0 = time.perf_counter()
                if kind == "events":
                    r = await client.post("/api/v1/events", content=json.dumps(batch()))
                    assert r.status_code == 202, r.text
                else:
                    r = await client.get("/" + kind)
                    assert r.status_code == 200, r.text
                if i >= a.warmup:
                    lat[kind].append((time.perf_counter() - t0) * 1000)

        tasks = []
        start = time.perf_counter()
        for i in range(a.batches):
            await asyncio.sleep(max(0.0, start + i / a.rps - time.perf_counter()))
            tasks.append(asyncio.create_task(one("events", i)))
            if i % 2 == 0:  # ~ rps/2 probes per second each
                tasks += [asyncio.create_task(one("health", i)), asyncio.create_task(one("ready", i))]
        await asyncio.gather(*tasks)
    for k, xs in lat.items():
        print(f"{k:7s} n={len(xs):5d} p50={statistics.median(xs):7.2f} p95={pct(xs, .95):7.2f} p99={pct(xs, .99):7.2f} ms")


if __name__ == "__main__":
    asyncio.run(main())
