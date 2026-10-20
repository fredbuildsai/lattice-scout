"""OpenAIAgentsDirectAdapter: D4 fallback using the OpenAI Agents SDK with the default OpenAI provider."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, JsonValue

from scout.adapters.llm.fake_llm import canonical_json
from scout.adapters.llm.metering import record_for
from scout.ports.llm import (
    AgentResult,
    AgentSpec,
    CallContext,
    LLMOutputInvalid,
    LLMUnavailable,
    ModelTier,
    TokenUsage,
)
from scout.ports.usage import LlmCallStatus, PriceTable, UsageMeterPort
from scout.shared import Clock, IdGenerator


class OpenAIAgentsDirectAdapter:
    """Implements ``LLMPort`` with ``agents.Runner``; one ``llm_call`` row per run (usage summed by the SDK)."""

    def __init__(
        self,
        prices: PriceTable,
        usage: UsageMeterPort,
        clock: Clock,
        ids: IdGenerator,
        model_for: Mapping[ModelTier, str],
    ) -> None:
        self._prices, self._usage, self._clock, self._ids, self._model_for = (
            prices,
            usage,
            clock,
            ids,
            model_for,
        )

    async def run[OutT: BaseModel](
        self, agent: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Run the agent through the Agents SDK and meter the result."""
        from agents import Agent, ModelSettings, RunConfig, Runner  # lazy: optional ``llm`` extra

        model = self._model_for[agent.tier]
        sdk_agent: Agent[Any] = Agent(
            name=agent.name,
            instructions=agent.instructions,
            model=model,
            output_type=agent.output_type,
            model_settings=ModelSettings(max_tokens=agent.max_output_tokens),
        )
        started = time.perf_counter()
        spec: AgentSpec[BaseModel] = agent
        try:
            result = await Runner.run(
                sdk_agent, canonical_json(input), run_config=RunConfig(trace_include_sensitive_data=False)
            )
        except Exception as exc:
            latency = int((time.perf_counter() - started) * 1000)
            self._usage.record_llm_call(
                record_for(
                    spec=spec,
                    ctx=ctx,
                    attempt=1,
                    model=model,
                    provider="openai",
                    route=agent.tier.value,
                    usage=TokenUsage(input_tokens=0, output_tokens=0),
                    status=LlmCallStatus.ERROR,
                    latency_ms=latency,
                    error_type=type(exc).__name__,
                    prices=self._prices,
                    clock=self._clock,
                    ids=self._ids,
                )  # fmt: skip
            )
            raise LLMUnavailable("OpenAI call failed") from exc
        latency = int((time.perf_counter() - started) * 1000)
        u = result.context_wrapper.usage
        inp, out = u.input_tokens, u.output_tokens
        usage = TokenUsage(
            input_tokens=inp,
            output_tokens=out,
            cached_input_tokens=min(u.input_tokens_details.cached_tokens, inp),
            reasoning_tokens=min(u.output_tokens_details.reasoning_tokens, out),
        )
        rec = record_for(
            spec=spec, ctx=ctx, attempt=1, model=model, provider="openai", route=agent.tier.value, usage=usage,
            status=LlmCallStatus.OK, latency_ms=latency, error_type=None, prices=self._prices,
            clock=self._clock, ids=self._ids,
        )  # fmt: skip
        self._usage.record_llm_call(rec)
        output = result.final_output
        if not isinstance(output, agent.output_type):
            raise LLMOutputInvalid(f"{agent.name} returned {type(output).__name__}")
        return AgentResult(
            output=output, model=model, route=agent.tier.value, usage=usage, cost_usd=rec.cost_usd,
            latency_ms=latency, attempts=1,
        )  # fmt: skip
