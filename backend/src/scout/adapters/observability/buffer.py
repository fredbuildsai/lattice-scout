"""Bounded in-memory buffer with a background batch writer. ``offer`` is sync and never raises."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger()

MAX_CONSECUTIVE_FAILURES = 3


class BufferedWriter[T]:
    """Collect items and hand them to ``sink`` in batches.

    Never blocks the caller: when full, items are dropped; at 80 % full, items rejected by ``sample`` are
    dropped first. The loop flushes every ``flush_ms`` or when ``batch_size`` items are waiting.
    """

    def __init__(
        self,
        sink: Callable[[list[T]], Awaitable[None]],
        *,
        name: str,
        capacity: int,
        flush_ms: int = 2000,
        batch_size: int = 500,
        sample: Callable[[T], bool] | None = None,
    ) -> None:
        self._sink, self.name = sink, name
        self._capacity, self._flush_s, self._batch = capacity, flush_ms / 1000, batch_size
        self._sample = sample
        self._queue: deque[T] = deque()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._stop = asyncio.Event()
        self._failures = 0
        self.delivered = self.dropped = self.sampled_out = self.failure_count = 0

    def __len__(self) -> int:
        return len(self._queue)

    def offer(self, item: T) -> None:
        """Queue ``item``; drop it if closed, full, or sampled out under pressure."""
        try:
            if self._closed:
                self.dropped += 1
                return
            n = len(self._queue)
            if n >= self._capacity:
                self.dropped += 1
                if self.dropped % 100 == 1:
                    log.warning("buffer_full_drop", buffer=self.name, dropped=self.dropped)
                return
            if self._sample is not None and n >= 0.8 * self._capacity and not self._sample(item):
                self.sampled_out += 1
                return
            self._queue.append(item)
        except Exception:
            self.dropped += 1

    def start(self) -> None:
        """Start the background flush loop (needs a running event loop)."""
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run(), name=f"buffer-{self.name}")

    async def _run(self) -> None:
        tick = min(self._flush_s, 0.25)
        last = time.monotonic()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=tick)
            except TimeoutError:
                pass
            if self._stop.is_set():
                break
            if len(self._queue) >= self._batch or (self._queue and time.monotonic() - last >= self._flush_s):
                await self._flush_once()
                last = time.monotonic()

    async def _flush_once(self) -> None:
        if not self._queue:
            return
        batch = [self._queue.popleft() for _ in range(min(self._batch, len(self._queue)))]
        try:
            await self._sink(batch)
        except Exception as exc:
            self._failures += 1
            self.failure_count += 1
            log.warning("buffer_flush_failed", buffer=self.name, size=len(batch), error=repr(exc)[:300])
            if self._failures >= MAX_CONSECUTIVE_FAILURES or len(self._queue) + len(batch) > self._capacity:
                self.dropped += len(batch)
                self._failures = 0
                log.error("buffer_batch_dropped", buffer=self.name, size=len(batch), error=str(exc))
            else:
                self._queue.extendleft(reversed(batch))
        else:
            self._failures = 0
            self.delivered += len(batch)

    async def flush(self) -> None:
        """Drain everything now (used on shutdown and in tests)."""
        guard = 0
        while self._queue and guard < 1000:
            before = len(self._queue)
            await self._flush_once()
            guard += 1
            if len(self._queue) >= before and self._failures == 0 and self.failure_count > 0:
                break

    async def close(self, deadline_s: float = 5.0) -> None:
        """Stop the loop and deliver what is left (bounded by ``deadline_s``). Later offers are dropped."""
        self._closed = True
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=deadline_s)  # lets an in-flight batch finish
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._task = None
        try:
            await asyncio.wait_for(self.flush(), timeout=deadline_s)
        except TimeoutError:
            log.error("buffer_close_timeout", buffer=self.name, left=len(self._queue))
        self.dropped += len(self._queue)
        self._queue.clear()
