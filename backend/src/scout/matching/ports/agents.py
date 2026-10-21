"""The three matching agents as one port (adapter: ``AgentsMatching`` over the agent factory)."""

from __future__ import annotations

from typing import Protocol

from scout.matching.domain.agent_io import (
    JointIdeaList,
    JointIdeasInput,
    MatchExplainerInput,
    MatchRationale,
    MethodExtractorInput,
    MethodSet,
)
from scout.ports.llm import AgentOutcome, CallContext


class MatchAgentsPort(Protocol):
    """Typed calls; each raises ``LLMUnavailable`` or ``LLMOutputInvalid``."""

    async def extract_methods(self, data: MethodExtractorInput, ctx: CallContext) -> AgentOutcome[MethodSet]:
        """MethodExtractor (fast)."""
        ...

    async def explain(self, data: MatchExplainerInput, ctx: CallContext) -> AgentOutcome[MatchRationale]:
        """MatchExplainer (fast)."""
        ...

    async def joint_ideas(self, data: JointIdeasInput, ctx: CallContext) -> AgentOutcome[JointIdeaList]:
        """JointIdeas (reasoning)."""
        ...
