import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from scout.adapters.observability.buffer import BufferedWriter


async def test_close_delivers_everything() -> None:
    got: list[int] = []

    async def sink(batch: list[int]) -> None:
        got.extend(batch)

    w = BufferedWriter[int](sink, name="t", capacity=100, flush_ms=10_000)
    for i in range(10):
        w.offer(i)
    await w.close()
    assert got == list(range(10))
    w.offer(99)
    assert w.dropped == 1


async def test_full_buffer_drops_without_raising() -> None:
    w = BufferedWriter[int](lambda b: asyncio.sleep(0), name="t", capacity=3)
    for i in range(10):
        w.offer(i)
    assert len(w) == 3 and w.dropped == 7


async def test_sampling_under_pressure_keeps_priority_items() -> None:
    w = BufferedWriter[str](lambda b: asyncio.sleep(0), name="t", capacity=10, sample=lambda s: s == "keep")
    for _ in range(8):
        w.offer("keep")
    w.offer("meh")
    w.offer("keep")
    assert w.sampled_out == 1 and len(w) == 9


async def test_failing_sink_retries_then_drops_and_never_raises() -> None:
    async def sink(batch: list[int]) -> None:
        raise RuntimeError("db down")

    w = BufferedWriter[int](sink, name="t", capacity=100)
    w.offer(1)
    for _ in range(3):
        await w._flush_once()
    assert w.dropped == 1 and len(w) == 0


async def test_slow_sink_does_not_block_offer() -> None:
    async def sink(batch: list[int]) -> None:
        await asyncio.sleep(1)

    w = BufferedWriter[int](sink, name="t", capacity=1000, flush_ms=10, batch_size=2)
    w.start()
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    for i in range(500):
        w.offer(i)
    assert loop.time() - t0 < 0.05
    w._stop.set()


@given(st.lists(st.sampled_from(["offer", "flush", "fail"]), max_size=60))
@settings(max_examples=50, deadline=None)
def test_accounting_invariant(ops: list[str]) -> None:
    async def scenario() -> None:
        fail = False

        async def sink(batch: list[int]) -> None:
            if fail:
                raise RuntimeError

        w = BufferedWriter[int](sink, name="t", capacity=8, batch_size=3)
        offered = 0
        for op in ops:
            if op == "offer":
                w.offer(offered)
                offered += 1
            elif op == "flush":
                await w._flush_once()
            else:
                fail = not fail
            assert len(w) <= 8
        assert w.delivered + w.dropped + w.sampled_out + len(w) == offered

    asyncio.run(scenario())


async def test_close_during_inflight_flush_loses_nothing() -> None:
    got: list[int] = []

    async def sink(batch: list[int]) -> None:
        await asyncio.sleep(0.15)
        got.extend(batch)

    w = BufferedWriter[int](sink, name="t", capacity=100, flush_ms=10, batch_size=100)
    w.start()
    w.offer(1)
    await asyncio.sleep(0.3)  # the loop is now inside the slow sink
    w.offer(2)
    await w.close()
    assert sorted(got) == [1, 2]
