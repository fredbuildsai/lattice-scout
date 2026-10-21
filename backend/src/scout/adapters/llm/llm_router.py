"""LlmRouterAdapter: typed agent calls over the in-house llmrouter-free package (ADR-013).

The router is synchronous and returns text, so each call sends ``[system=instructions, user=input JSON]`` with
JSON-schema constrained output and validates it into the agent's Pydantic type. Every provider attempt becomes
one ``llm_call`` row (token counts are read from LiteLLM's usage inside ``MeteringCompletion``).
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, JsonValue

from scout.adapters.llm.fake_llm import canonical_json
from scout.adapters.llm.metering import provider_of, record_for
from scout.adapters.llm.vault_policy import VaultPolicy
from scout.ports.llm import (
    AgentResult,
    AgentSpec,
    CallContext,
    DataClass,
    LLMUnavailable,
    ModelTier,
    TokenUsage,
)
from scout.ports.usage import LlmCallStatus, PriceTable, UsageMeterPort
from scout.shared import Clock, IdGenerator


@dataclass
class Attempt:
    """One provider attempt observed by ``MeteringCompletion``."""

    model: str
    usage: TokenUsage
    latency_ms: int
    error_type: str | None = None
    rate_limited: bool = False


_local = threading.local()


def _attempts() -> list[Attempt]:
    if not hasattr(_local, "attempts"):
        _local.attempts = []
    attempts: list[Attempt] = _local.attempts
    return attempts


def _usage_from(response: Any) -> TokenUsage:
    u = getattr(response, "usage", None)

    def num(obj: Any, name: str) -> int | None:
        v = getattr(obj, name, None) if obj is not None else None
        return int(v) if isinstance(v, (int, float)) else None

    inp, out = num(u, "prompt_tokens") or 0, num(u, "completion_tokens") or 0
    cached = num(getattr(u, "prompt_tokens_details", None), "cached_tokens")
    reasoning = num(getattr(u, "completion_tokens_details", None), "reasoning_tokens")
    return TokenUsage(
        input_tokens=inp,
        output_tokens=out,
        cached_input_tokens=min(cached, inp) if cached is not None else None,
        reasoning_tokens=min(reasoning, out) if reasoning is not None else None,
    )


class MeteringCompletion:
    """Wraps a ``litellm.completion``-compatible function and notes every attempt (thread-local)."""

    def __init__(self, completion: Callable[..., Any] | None = None) -> None:
        self._completion = completion

    def _fn(self) -> Callable[..., Any]:
        if self._completion is None:
            import litellm  # lazy: slow import, optional dependency

            litellm.suppress_debug_info = True
            self._completion = litellm.completion
        return self._completion

    def __call__(self, **kwargs: Any) -> Any:
        started = time.perf_counter()
        model = str(kwargs.get("model", ""))
        guard: VaultPolicy | None = getattr(_local, "vault_guard", None)
        if guard is not None:
            guard.check_model(model)  # raises VaultDataRefused before anything is sent
        try:
            response = self._fn()(**kwargs)
        except Exception as exc:
            _attempts().append(
                Attempt(
                    model,
                    TokenUsage(input_tokens=0, output_tokens=0),
                    int((time.perf_counter() - started) * 1000),
                    type(exc).__name__,
                    rate_limited=getattr(exc, "status_code", None) == 429,
                )  # fmt: skip
            )
            raise
        _attempts().append(Attempt(model, _usage_from(response), int((time.perf_counter() - started) * 1000)))
        return response


class LlmRouterAdapter:
    """Implements ``LLMPort`` over an ``llmrouter_free.LLMRouter`` built with ``completion_fn=MeteringCompletion()``."""

    def __init__(
        self,
        router: Any,
        prices: PriceTable,
        usage: UsageMeterPort,
        clock: Clock,
        ids: IdGenerator,
        route_for: Mapping[ModelTier, str],
        executor: ThreadPoolExecutor,
        vault_policy: VaultPolicy | None = None,
    ) -> None:
        self._vault = vault_policy or VaultPolicy()
        self._router, self._prices, self._usage = router, prices, usage
        self._clock, self._ids, self._route_for, self._executor = clock, ids, route_for, executor

    def _call(
        self, agent: AgentSpec[Any], messages: list[dict[str, str]], route: str
    ) -> tuple[Any, list[Attempt]]:
        from llmrouter_free import json_schema_response_format, json_validator

        _local.attempts = []
        _local.vault_guard = self._vault if agent.data_class is DataClass.VAULT else None
        try:
            kwargs: dict[str, Any] = {
                "validate": json_validator(agent.output_type),
                "temperature": agent.temperature,
                "max_tokens": agent.max_output_tokens,
                "use_cache": False,
            }
            if agent.strict_schema:
                # strict=False: Groq rejects strict schemas; the schema is still sent as a hint and validated by us
                kwargs["response_format"] = json_schema_response_format(agent.output_type, strict=False)
            else:
                kwargs["json_mode"] = True
            result = self._router.complete(route, messages, **kwargs)
            return result, list(_local.attempts)
        except Exception as exc:
            exc.attempts = list(_local.attempts)  # type: ignore[attr-defined]
            raise
        finally:
            _local.vault_guard = None

    async def run[OutT: BaseModel](
        self, agent: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Run the agent; record one row per attempt; raise ``LLMUnavailable`` if the route is exhausted."""
        route = self._vault.route_for(agent.name, agent.data_class, self._route_for[agent.tier])
        messages = [
            {"role": "system", "content": agent.instructions},
            {"role": "user", "content": canonical_json(input)},
        ]
        loop = asyncio.get_running_loop()
        started = time.perf_counter()
        error: Exception | None = None
        result: Any = None
        try:
            result, attempts = await loop.run_in_executor(
                self._executor, contextvars.copy_context().run, self._call, agent, messages, route
            )
        except Exception as exc:
            error = exc
            attempts = getattr(exc, "attempts", [])
        spec: AgentSpec[BaseModel] = agent
        total = TokenUsage(input_tokens=0, output_tokens=0)
        cost = Decimal(0)
        for i, a in enumerate(attempts, start=1):
            last_ok = error is None and i == len(attempts)
            if a.error_type:
                status = LlmCallStatus.RATE_LIMITED if a.rate_limited else LlmCallStatus.ERROR
            else:
                status = LlmCallStatus.OK if last_ok else LlmCallStatus.INVALID_OUTPUT
            rec = record_for(
                spec=spec, ctx=ctx, attempt=i, model=a.model, provider=provider_of(a.model), route=route,
                usage=a.usage, status=status, latency_ms=a.latency_ms, error_type=a.error_type,
                prices=self._prices, clock=self._clock, ids=self._ids,
            )  # fmt: skip
            self._usage.record_llm_call(rec)
            total = total.plus(a.usage)
            cost += rec.cost_usd
        if error is not None:
            raise LLMUnavailable(f"All deployments failed for route {route}") from error
        return AgentResult(
            output=result.parsed, model=result.model, route=route, usage=total, cost_usd=cost,
            latency_ms=int((time.perf_counter() - started) * 1000), attempts=len(attempts),
        )  # fmt: skip
