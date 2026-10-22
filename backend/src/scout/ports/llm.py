"""LLM port: typed agents run through any backend (fake, llm-router, OpenAI Agents SDK)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from scout.shared import InfrastructureError
from scout.shared.confidential import (
    DataClass,
)
from scout.shared.context import RequestContext

__all__ = ["DataClass"]  # re-exported: adapters and tests import it from the LLM port


class ModelTier(StrEnum):
    """Which class of model an agent needs; mapped to a concrete route in configuration."""

    FAST = "fast"
    REASONING = "reasoning"


@dataclass(frozen=True)
class AgentSpec[OutT: BaseModel]:
    """A runtime agent: prompt, version, tier and the Pydantic type of its output."""

    name: str
    prompt_version: str
    tier: ModelTier
    instructions: str
    output_type: type[OutT]
    temperature: float = 0.2
    max_output_tokens: int = 4096
    strict_schema: bool = True
    data_class: DataClass = (
        DataClass.CONFIDENTIAL
    )  # fail closed: only an agent that never sees private text is published


class CallContext(BaseModel):
    """Attribution of a call (for usage tracking)."""

    model_config = ConfigDict(frozen=True)

    feature: str
    account_id: UUID | None = None
    session_id: UUID | None = None
    run_id: UUID | None = None

    @classmethod
    def from_request(cls, ctx: RequestContext, feature: str) -> Self:
        """Build from the request context; a malformed session id becomes None."""
        try:
            session = UUID(ctx.session_id) if ctx.session_id else None
        except ValueError:
            session = None
        return cls(feature=feature, account_id=ctx.account_id, session_id=session, run_id=ctx.run_id)


class TokenUsage(BaseModel):
    """Token counts. ``None`` means the provider did not report the field."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens")
        return self

    def plus(self, other: TokenUsage) -> TokenUsage:
        """Sum two usages (``None`` + ``None`` stays ``None``)."""

        def add(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=add(self.cached_input_tokens, other.cached_input_tokens),
            reasoning_tokens=add(self.reasoning_tokens, other.reasoning_tokens),
        )


class AgentResult[OutT: BaseModel](BaseModel):
    """Validated output plus accounting (usage summed over attempts)."""

    model_config = ConfigDict(frozen=True)

    output: OutT
    model: str
    route: str
    usage: TokenUsage
    cost_usd: Decimal
    latency_ms: int
    attempts: int


class LLMUnavailable(InfrastructureError):
    """Every deployment of the route failed."""

    code = "llm_unavailable"
    title = "Language model unavailable"


class ConfidentialDataRefused(InfrastructureError):
    """Backstop: a confidential call reached an adapter without an authorized endpoint, so nothing was sent.

    The agent factory and the use cases check ``confidential_allowed`` first and use the published form
    (ADR 016), so users never see this."""

    code = "confidential_data_refused"
    http_status = 503
    title = "No authorized language model endpoint for confidential data"


class LLMOutputInvalid(InfrastructureError):
    """The model (or a fixture) produced output that does not match the schema."""

    code = "llm_output_invalid"
    http_status = 502
    title = "Language model returned invalid output"


class ConfidentialGate(Protocol):
    """Whether confidential data may be sent through this backend (ADR 016)."""

    def confidential_allowed(self, tier: ModelTier | None = None) -> bool:
        """True when ``tier`` (``None``: every tier) is served by authorized deployments only for confidential calls."""
        ...


class LLMPort(ConfidentialGate, Protocol):
    """Run one typed agent call."""

    async def run[OutT: BaseModel](
        self, agent: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Execute ``agent`` on ``input``; record usage; raise ``LLMUnavailable`` / ``LLMOutputInvalid``."""
        ...


@dataclass(frozen=True)
class AgentOutcome[T]:
    """A model output with its accounting (the run ledger charges ``cost_usd`` and the tokens)."""

    output: T
    prompt_version: str
    model: str
    cost_usd: Decimal
    usage: TokenUsage
