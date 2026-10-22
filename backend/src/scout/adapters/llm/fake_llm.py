"""Deterministic offline LLM: outputs come from fixtures or scripted overrides; usage is synthetic."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, JsonValue, ValidationError

from scout.adapters.llm.metering import record_for
from scout.ports.llm import (
    AgentResult,
    AgentSpec,
    CallContext,
    ConfidentialDataRefused,
    DataClass,
    LLMOutputInvalid,
    ModelTier,
    TokenUsage,
)
from scout.ports.usage import LlmCallStatus, PriceTable, UsageMeterPort
from scout.shared import Clock, IdGenerator, InfrastructureError


class FakeFixtureMissing(InfrastructureError):
    """No fixture or script exists for the call (tests should provide one)."""

    code = "fake_fixture_missing"
    title = "Missing LLM fixture"


def canonical_json(value: BaseModel | Mapping[str, JsonValue]) -> str:
    """Stable JSON used for fixture hashing and token estimation."""
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_hash(value: BaseModel | Mapping[str, JsonValue]) -> str:
    """First 16 hex chars of sha256 over the canonical input JSON (the fixture file name)."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()[:16]


class FakeLLM:
    """Implements ``LLMPort`` without any network."""

    def __init__(
        self, fixtures_dir: Path, usage: UsageMeterPort, prices: PriceTable, clock: Clock, ids: IdGenerator
    ) -> None:
        self._dir, self._usage, self._prices, self._clock, self._ids = fixtures_dir, usage, prices, clock, ids
        self._scripts: dict[str, Any] = {}
        self.confidential_ok = True  # tests set False to emulate an installation without authorized endpoints

    def confidential_allowed(self, tier: ModelTier | None = None) -> bool:
        """The fake never leaves the process, so it may see everything unless a test narrows it."""
        return self.confidential_ok

    def script(self, agent_name: str, output: BaseModel | Mapping[str, Any] | Callable[[Any], Any]) -> None:
        """Override the output of an agent (a value, or a function of the input)."""
        self._scripts[agent_name] = output

    def _raw_output(self, spec: AgentSpec[Any], input: BaseModel | Mapping[str, JsonValue]) -> Any:
        if spec.name in self._scripts:
            out = self._scripts[spec.name]
            return out(input) if callable(out) else out
        folder = self._dir / spec.name
        candidates = [folder / f"{input_hash(input)}.json", folder / "default.json"]
        for path in candidates:
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FakeFixtureMissing(
            "No fixture for " + spec.name, extra={"expected": ", ".join(map(str, candidates))}
        )

    async def run[OutT: BaseModel](
        self, agent: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Return the fixture/script output validated against ``agent.output_type``; record one llm_call."""
        if agent.data_class is DataClass.CONFIDENTIAL and not self.confidential_ok:
            raise ConfidentialDataRefused(
                "fake: no authorized endpoint"
            )  # same backstop as the real adapters
        raw = self._raw_output(agent, input)
        try:
            output = raw if isinstance(raw, agent.output_type) else agent.output_type.model_validate(raw)
        except ValidationError as exc:
            raise LLMOutputInvalid(
                f"Fixture for {agent.name} does not match {agent.output_type.__name__}"
            ) from exc
        usage = TokenUsage(
            input_tokens=math.ceil(len(agent.instructions + canonical_json(input)) / 4),
            output_tokens=math.ceil(len(canonical_json(output)) / 4),
            cached_input_tokens=0,
            reasoning_tokens=0,
        )
        rec = record_for(
            spec=agent, ctx=ctx, attempt=1, model="fake", provider="fake", route=agent.tier.value, usage=usage,
            status=LlmCallStatus.OK, latency_ms=0, error_type=None, prices=self._prices, clock=self._clock,
            ids=self._ids,
        )  # fmt: skip
        self._usage.record_llm_call(rec)
        return AgentResult(
            output=output, model="fake", route=agent.tier.value, usage=usage, cost_usd=rec.cost_usd,
            latency_ms=0, attempts=1,
        )  # fmt: skip
