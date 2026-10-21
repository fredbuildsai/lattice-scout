"""Agent factory: prompts from ``prompts/``, one traced and error-mapped way to run an agent.

Input and output text never reach spans, logs, exceptions or job errors (vault text must not leak): errors are
re-raised ``from None`` with a fixed message, because a chained pydantic ``ValidationError`` embeds the input.
"""

from __future__ import annotations

import dataclasses
import re
import time
from collections.abc import Mapping
from pathlib import Path

import httpx
import structlog
from opentelemetry import trace
from pydantic import BaseModel, JsonValue, ValidationError

from scout.adapters.llm.fake_llm import canonical_json
from scout.adapters.llm.prompts import load_agent_spec
from scout.ports.llm import (
    AgentResult,
    AgentSpec,
    CallContext,
    ConfidentialDataRefused,
    DataClass,
    LLMOutputInvalid,
    LLMPort,
    LLMUnavailable,
    ModelTier,
)
from scout.shared import InfrastructureError, ValidationFailed
from scout.shared.confidential import AgentInput, input_for_route

log = structlog.get_logger("scout.agents")
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class AgentFactory:
    """Build specs (cached) and run agents on an ``LLMPort``."""

    def __init__(
        self,
        llm: LLMPort,
        prompts_dir: Path,
        placeholders: Mapping[str, str] | None = None,
        tracer: trace.Tracer | None = None,
    ) -> None:
        self._llm, self._dir, self._placeholders = llm, prompts_dir, dict(placeholders or {})
        self._tracer = tracer or trace.get_tracer("scout.agents")
        self._cache: dict[str, AgentSpec[BaseModel]] = {}

    def spec[OutT: BaseModel](
        self, name: str, output_type: type[OutT], **overrides: object
    ) -> AgentSpec[OutT]:
        """Load ``prompts/<name>.md`` and fill ``{{placeholders}}``.

        Raises:
            ValidationFailed: unknown placeholder, malformed front-matter or a different ``output_type``.
        """
        cached = self._cache.get(name)
        if cached is not None and not overrides and cached.output_type is output_type:
            return cached  # type: ignore[return-value]
        raw = load_agent_spec(self._dir, name, output_type, **overrides)

        def fill(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in self._placeholders:
                raise ValidationFailed(f"{name}: unfilled placeholder {key}")
            return self._placeholders[key]

        spec = AgentSpec(
            name=raw.name, prompt_version=raw.prompt_version, tier=raw.tier,
            instructions=_PLACEHOLDER.sub(fill, raw.instructions), output_type=raw.output_type,
            temperature=raw.temperature, max_output_tokens=raw.max_output_tokens, strict_schema=raw.strict_schema,
            data_class=raw.data_class,
        )  # fmt: skip
        if not overrides:
            self._cache[name] = spec
        return spec

    def confidential_allowed(self, tier: ModelTier | None = None) -> bool:
        """Whether confidential data may be sent for ``tier`` (``None``: both tiers). Use cases ask this first."""
        return self._llm.confidential_allowed(tier)

    @staticmethod
    def version(spec: AgentSpec[BaseModel]) -> str:
        """``name@version``, stored with every result."""
        return f"{spec.name}@{spec.prompt_version}"

    async def run[OutT: BaseModel](
        self, spec: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Run one agent call inside the span ``agent.run``.

        Raises:
            LLMOutputInvalid: the output did not match the schema.
            LLMUnavailable: any other provider or infrastructure failure.
        """
        started = time.perf_counter()
        authorized = self._llm.confidential_allowed(spec.tier)
        planned: tuple[BaseModel | Mapping[str, JsonValue], DataClass] | None
        if isinstance(input, AgentInput):
            planned = input_for_route(spec.data_class, input, authorized=authorized)
        else:  # a plain model or mapping cannot be classified, so it is confidential, whatever the agent's class
            planned = (input, DataClass.CONFIDENTIAL) if authorized else None
        if planned is None:
            log.warning("agent_skipped", agent=self.version(spec), reason="no_published_form")
            raise ConfidentialDataRefused(f"{spec.name}: no published form of this input") from None
        input, data_class = planned
        spec = dataclasses.replace(spec, data_class=data_class)
        input_chars = len(canonical_json(input))
        with self._tracer.start_as_current_span("agent.run") as span:
            span.set_attribute("agent.name", spec.name)
            span.set_attribute("agent.prompt_version", self.version(spec))
            span.set_attribute("agent.tier", spec.tier.value)
            span.set_attribute("agent.input_chars", input_chars)
            try:
                result = await self._llm.run(spec, input, ctx)
            except (LLMOutputInvalid, ValidationError):
                self._failed(span, spec, "invalid", started)
                raise LLMOutputInvalid(f"{spec.name}: output invalid") from None
            except (InfrastructureError, httpx.HTTPError, OSError):
                self._failed(span, spec, "unavailable", started)
                raise LLMUnavailable(f"{spec.name}: unavailable") from None
            span.set_attribute("agent.model", result.model)
            span.set_attribute("agent.route", result.route)
            span.set_attribute("agent.attempts", result.attempts)
            span.set_attribute("agent.tokens", result.usage.input_tokens + result.usage.output_tokens)
            span.set_attribute("agent.cost_usd", float(result.cost_usd))
            log.info(
                "agent_done", agent=self.version(spec), tier=spec.tier.value, model=result.model, route=result.route,
                attempts=result.attempts, tokens_in=result.usage.input_tokens, tokens_out=result.usage.output_tokens,
                cost_usd=float(result.cost_usd), input_chars=input_chars,
                ms=round((time.perf_counter() - started) * 1000),
            )  # fmt: skip
            return result

    @staticmethod
    def _failed(span: trace.Span, spec: AgentSpec[BaseModel], outcome: str, started: float) -> None:
        span.set_attribute("agent.outcome", outcome)
        log.warning("agent_failed", agent=f"{spec.name}@{spec.prompt_version}", outcome=outcome,
                    ms=round((time.perf_counter() - started) * 1000))  # fmt: skip
