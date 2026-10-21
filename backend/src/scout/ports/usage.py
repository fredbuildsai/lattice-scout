"""Usage metering port (LLM + external API calls) and the price table."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from scout.ports.llm import TokenUsage

_MILLION = Decimal(1_000_000)
_QUANT = Decimal("0.00000001")


class LlmCallStatus(StrEnum):
    """Outcome of one provider attempt."""

    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class ExternalService(StrEnum):
    """External services whose calls we meter."""

    OPENALEX = "openalex"
    EMAIL = "email"
    STORAGE = "storage"


class LlmCallRecord(BaseModel):
    """One row of ``llm_call``."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    ts: AwareDatetime
    account_id: UUID | None = None
    session_id: UUID | None = None
    run_id: UUID | None = None
    feature: str
    agent: str
    route: str
    provider: str
    model: str
    attempt: int = 1
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    latency_ms: int
    cost_usd: Decimal
    status: LlmCallStatus
    error_type: str | None = None
    prompt_version: str
    trace_id: str | None = None


class ExternalCallRecord(BaseModel):
    """One row of ``external_call``. ``endpoint`` is a route template without ids or query values."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    ts: AwareDatetime
    account_id: UUID | None = None
    session_id: UUID | None = None
    run_id: UUID | None = None
    service: ExternalService
    endpoint: str
    status: Literal["ok", "error", "timeout"]
    http_status: int | None = None
    latency_ms: int
    cost_usd: Decimal = Decimal(0)
    cache_hit: bool = False
    trace_id: str | None = None


class ModelPrice(BaseModel):
    """USD per 1M tokens."""

    model_config = ConfigDict(frozen=True)

    input: Decimal = Decimal(0)
    output: Decimal = Decimal(0)
    cached_input: Decimal = Decimal(0)
    reasoning: Decimal = Decimal(0)


class PriceTable(BaseModel):
    """Versioned prices. Cached tokens are part of input and reasoning tokens part of output."""

    model_config = ConfigDict(frozen=True)

    version: str
    models: dict[str, ModelPrice]
    default: ModelPrice = ModelPrice()

    def price_for(self, model: str) -> tuple[ModelPrice, bool]:
        """Return ``(price, known)``: exact match, then the part after the last ``/``, else the default."""
        if model in self.models:
            return self.models[model], True
        tail = model.rsplit("/", 1)[-1]
        if tail in self.models:
            return self.models[tail], True
        return self.default, False

    def cost(self, model: str, usage: TokenUsage) -> Decimal:
        """Cost in USD, quantized to 1e-8. Reasoning tokens are already inside ``output_tokens``."""
        p, _ = self.price_for(model)
        cached = usage.cached_input_tokens or 0
        total = (
            (usage.input_tokens - cached) * p.input + cached * p.cached_input + usage.output_tokens * p.output
        ) / _MILLION
        return total.quantize(_QUANT)


class UsageMeterPort(Protocol):
    """Non-blocking sinks: never raise, never perform I/O on the caller's path."""

    def record_llm_call(self, rec: LlmCallRecord) -> None:
        """Buffer one LLM call."""
        ...

    def record_external_call(self, rec: ExternalCallRecord) -> None:
        """Buffer one external API call."""
        ...
