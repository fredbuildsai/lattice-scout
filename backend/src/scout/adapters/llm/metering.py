"""Helpers shared by every LLM adapter to build ``llm_call`` records."""

from __future__ import annotations

from opentelemetry import trace
from pydantic import BaseModel

from scout.ports.llm import AgentSpec, CallContext, TokenUsage
from scout.ports.usage import LlmCallRecord, LlmCallStatus, PriceTable
from scout.shared import Clock, IdGenerator


def provider_of(model: str) -> str:
    """LiteLLM-style prefix (``openai/gpt-5`` -> ``openai``); bare model names default to ``openai``."""
    return model.split("/", 1)[0] if "/" in model else "openai"


def current_trace_id() -> str | None:
    """Hex trace id of the active OpenTelemetry span, if any."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def record_for(
    *,
    spec: AgentSpec[BaseModel],
    ctx: CallContext,
    attempt: int,
    model: str,
    provider: str,
    route: str,
    usage: TokenUsage,
    status: LlmCallStatus,
    latency_ms: int,
    error_type: str | None,
    prices: PriceTable,
    clock: Clock,
    ids: IdGenerator,
) -> LlmCallRecord:
    """Build one record, costing it with the price table."""
    return LlmCallRecord(
        id=ids.new(),
        ts=clock.now(),
        account_id=ctx.account_id,
        session_id=ctx.session_id,
        run_id=ctx.run_id,
        feature=ctx.feature,
        agent=spec.name,
        route=route,
        provider=provider,
        model=model,
        attempt=attempt,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        latency_ms=latency_ms,
        cost_usd=prices.cost(model, usage),
        status=status,
        error_type=error_type,
        prompt_version=spec.prompt_version,
        trace_id=current_trace_id(),
    )
