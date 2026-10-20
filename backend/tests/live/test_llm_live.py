"""Live LLM calls. Skipped unless run with ``-m needs_network`` and OPENAI_API_KEY set."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.fakes import InMemoryUsageMeter

from scout.adapters.observability.prices import load_price_table
from scout.ports.llm import AgentSpec, CallContext, ModelTier
from scout.shared import FixedClock, SequentialIdGenerator

pytestmark = [
    pytest.mark.needs_network,
    pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set"),
]


class Pong(BaseModel):
    message: str


SPEC = AgentSpec(
    name="Ping",
    prompt_version="live",
    tier=ModelTier.FAST,
    instructions="Reply with message='pong'.",
    output_type=Pong,
)


async def test_openai_direct_adapter_records_tokens() -> None:
    from scout.adapters.llm.openai_direct import OpenAIAgentsDirectAdapter

    meter = InMemoryUsageMeter()
    prices = load_price_table(Path("config/llm_prices.yaml"))
    clock, ids = FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator()
    llm = OpenAIAgentsDirectAdapter(
        prices, meter, clock, ids, {ModelTier.FAST: "gpt-5-mini", ModelTier.REASONING: "gpt-5"}
    )
    r = await llm.run(SPEC, {"q": "ping"}, CallContext(feature="live"))
    assert r.usage.input_tokens > 0 and r.usage.output_tokens > 0 and len(meter.llm) == 1


async def test_llm_router_adapter_records_tokens() -> None:
    pytest.importorskip("llmrouter_free")
    from concurrent.futures import ThreadPoolExecutor

    from llmrouter_free import LLMRouter, load_routes

    from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion

    meter = InMemoryUsageMeter()
    prices = load_price_table(Path("config/llm_prices.yaml"))
    router = LLMRouter(
        load_routes(Path("config/llm_router.yaml")),
        completion_fn=MeteringCompletion(),
        allow_paid=True,
        max_usd_per_day=1,
    )
    llm = LlmRouterAdapter(
        router, prices, meter, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator(),
        {ModelTier.FAST: "fast", ModelTier.REASONING: "reasoning"}, ThreadPoolExecutor(2),
    )  # fmt: skip
    r = await llm.run(SPEC, {"q": "ping"}, CallContext(feature="live"))
    assert r.usage.input_tokens > 0 and meter.llm
