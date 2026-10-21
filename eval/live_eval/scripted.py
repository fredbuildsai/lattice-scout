"""Scripted offline LLM backend for the live harness (tests and ``--dry-run``). No network, no keys.

It replays each case's ``ideal`` output and can misbehave on purpose:
``perfect`` | ``invalid_json`` (30% of calls fail schema validation) | ``approve_all`` (the Critic verifies every
claim and keeps every draft) | ``strict_reject`` (rejects the schema response format like Groq does, works in JSON
mode) | ``rate_limited`` (every 4th request is a 429 and is retried once) | ``leaky`` (MatchExplainer and JointIdeas
copy a public title into the text).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, JsonValue

from live_eval.cases import EvalCase
from live_eval.runner import Attempts
from scout.adapters.llm.fake_llm import canonical_json
from scout.ports.llm import (
    AgentResult,
    AgentSpec,
    CallContext,
    LLMOutputInvalid,
    LLMUnavailable,
    ModelTier,
    TokenUsage,
)

BEHAVIORS = ("perfect", "invalid_json", "approve_all", "strict_reject", "rate_limited", "leaky")


class ScriptedLLM:
    """Implements ``LLMPort`` by replaying ideal outputs."""

    def __init__(self, cases: list[EvalCase], attempts: Attempts, behavior: str = "perfect") -> None:
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown behavior {behavior}")
        self._ideal = {(c.agent, canonical_json(c.input)): c for c in cases}
        self._attempts, self.behavior, self.calls = attempts, behavior, 0
        self.confidential_ok = True

    def confidential_allowed(self, tier: ModelTier | None = None) -> bool:
        """The scripted backend never leaves the process."""
        return self.confidential_ok

    def _output(self, agent: AgentSpec[Any], case: EvalCase) -> dict[str, Any]:
        out = copy.deepcopy(case.ideal)
        if self.behavior == "approve_all" and agent.name == "Critic":
            for c in out["claims"]:
                c.update(status="verified", reason=None, fix=None)
            out["keep"] = True
        if (
            self.behavior == "leaky"
            and agent.name in ("MatchExplainer", "JointIdeas")
            and case.meta.get("titles")
        ):
            leak = case.meta["titles"][0]
            if agent.name == "MatchExplainer":
                out["for_a"]["why"] = leak[:290]
            else:
                out["ideas"][0]["question"] = leak[:240]
        return out

    async def run[OutT: BaseModel](
        self, agent: AgentSpec[OutT], input: BaseModel | Mapping[str, JsonValue], ctx: CallContext
    ) -> AgentResult[OutT]:
        """Replay the ideal output, or fail the way the behavior says."""
        self.calls += 1
        n = self.calls
        case = self._ideal[(agent.name, canonical_json(input))]
        if self.behavior == "strict_reject" and agent.strict_schema:
            self._attempts.note("error", "BadRequestError")
            raise LLMUnavailable("scripted: schema response format rejected")
        if self.behavior == "rate_limited" and n % 4 == 0:
            self._attempts.note("rate_limited", "RateLimitError")
        if self.behavior == "invalid_json" and n % 10 < 3:
            self._attempts.note("invalid_output")
            raise LLMOutputInvalid("scripted: not valid JSON")
        self._attempts.note("ok")
        output = agent.output_type.model_validate(self._output(agent, case))
        return AgentResult(
            output=output, model=f"scripted-{self.behavior}", route=agent.tier.value,
            usage=TokenUsage(input_tokens=100, output_tokens=50), cost_usd=0, latency_ms=1,
            attempts=2 if (self.behavior == "rate_limited" and n % 4 == 0) else 1,
        )  # fmt: skip
