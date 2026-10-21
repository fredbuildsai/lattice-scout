"""LlmRouterAdapter vs the real llmrouter-free router with a scripted completion function (offline)."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("llmrouter_free")

from llmrouter_free import LLMRouter
from tests.fakes import InMemoryUsageMeter

from scout.adapters.llm.llm_router import LlmRouterAdapter, MeteringCompletion
from scout.ports.llm import AgentSpec, CallContext, LLMUnavailable, ModelTier
from scout.ports.usage import LlmCallStatus, ModelPrice, PriceTable
from scout.shared import FixedClock, SequentialIdGenerator


class Out(BaseModel):
    answer: str


def _resp(content: str, pin: int = 100, pout: int = 20, cached: int = 40, reasoning: int = 5) -> Any:
    usage = SimpleNamespace(
        prompt_tokens=pin, completion_tokens=pout,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
    )  # fmt: skip
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)


class RateLimit(Exception):
    status_code = 429


def _adapter(script: list[Any]) -> tuple[LlmRouterAdapter, InMemoryUsageMeter]:
    os.environ["TEST_KEY"] = "x"
    calls = iter(script)

    def completion(**kwargs: Any) -> Any:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    cfg = {
        "deployments": [
            {"name": "a", "model": "openai/gpt-x", "api_key_env": "TEST_KEY"},
            {"name": "b", "model": "openai/gpt-y", "api_key_env": "TEST_KEY"},
        ],
        "routes": {"fast": ["a", "b"], "reasoning": ["a"]},
        "cooldown": {"rate_limit_seconds": 0, "error_seconds": 0},
    }
    router = LLMRouter(cfg, completion_fn=MeteringCompletion(completion), sleep=lambda s: None)
    meter = InMemoryUsageMeter()
    prices = PriceTable(version="t", models={"gpt-x": ModelPrice(input=Decimal(1), output=Decimal(2))})
    adapter = LlmRouterAdapter(
        router, prices, meter, FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator(),
        {ModelTier.FAST: "fast", ModelTier.REASONING: "reasoning"}, ThreadPoolExecutor(2),
    )  # fmt: skip
    return adapter, meter


SPEC = AgentSpec(name="T", prompt_version="1", tier=ModelTier.FAST, instructions="i", output_type=Out)


async def test_success_records_tokens_incl_cached_and_reasoning() -> None:
    adapter, meter = _adapter([_resp('{"answer": "42"}')])
    r = await adapter.run(SPEC, {"q": 1}, CallContext(feature="f"))
    assert r.output.answer == "42" and r.attempts == 1
    [rec] = meter.llm
    assert (rec.input_tokens, rec.output_tokens, rec.cached_input_tokens, rec.reasoning_tokens) == (
        100,
        20,
        40,
        5,
    )
    assert rec.status == LlmCallStatus.OK and rec.provider == "openai" and rec.cost_usd > 0


async def test_failover_records_one_row_per_attempt() -> None:
    adapter, meter = _adapter([RateLimit("429"), _resp('{"answer": "x"}')])
    r = await adapter.run(SPEC, {}, CallContext(feature="f"))
    assert r.attempts == 2
    assert [m.status for m in meter.llm] == [LlmCallStatus.RATE_LIMITED, LlmCallStatus.OK]
    assert r.usage.input_tokens == 100


async def test_invalid_output_then_valid() -> None:
    adapter, meter = _adapter([_resp("not json"), _resp('{"answer": "ok"}')])
    await adapter.run(SPEC, {}, CallContext(feature="f"))
    assert [m.status for m in meter.llm] == [LlmCallStatus.INVALID_OUTPUT, LlmCallStatus.OK]


async def test_exhausted_records_rows_then_raises() -> None:
    adapter, meter = _adapter([RuntimeError("boom"), RuntimeError("boom")] * 3)
    with pytest.raises(LLMUnavailable):
        await adapter.run(SPEC, {}, CallContext(feature="f"))
    assert meter.llm and all(m.status == LlmCallStatus.ERROR for m in meter.llm)
