"""The five discovery agents as one port (adapter: ``AgentsDiscovery`` over the agent factory)."""

from __future__ import annotations

from typing import Protocol

from scout.discovery.domain.agent_io import (
    ClusterLabel,
    ClusterLabelInput,
    CriticInput,
    CriticVerdict,
    GapAnalysis,
    GapContext,
    InsightTopicTaggerInput,
    InsightTopicTags,
    QueryPlan,
    QueryPlannerInput,
)
from scout.ports.llm import AgentOutcome, CallContext, ConfidentialGate

__all__ = ["AgentOutcome", "DiscoveryAgentsPort"]


class DiscoveryAgentsPort(ConfidentialGate, Protocol):
    """Typed calls; each raises ``LLMUnavailable`` or ``LLMOutputInvalid`` (transient).

    ``confidential_allowed()`` says whether vault material may be put into the inputs at all (ADR 016)."""

    async def plan_queries(self, data: QueryPlannerInput, ctx: CallContext) -> AgentOutcome[QueryPlan]:
        """QueryPlanner (fast)."""
        ...

    async def label_cluster(self, data: ClusterLabelInput, ctx: CallContext) -> AgentOutcome[ClusterLabel]:
        """ClusterLabeler (fast)."""
        ...

    async def tag_insights(
        self, data: InsightTopicTaggerInput, ctx: CallContext
    ) -> AgentOutcome[InsightTopicTags]:
        """InsightTopicTagger (fast)."""
        ...

    async def analyse(self, data: GapContext, ctx: CallContext) -> AgentOutcome[GapAnalysis]:
        """GapAnalyst (reasoning)."""
        ...

    async def critique(self, data: CriticInput, ctx: CallContext) -> AgentOutcome[CriticVerdict]:
        """Critic (reasoning)."""
        ...


__all__ = ["AgentOutcome", "DiscoveryAgentsPort"]
