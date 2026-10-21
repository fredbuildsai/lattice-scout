"""``AgentsDiscovery``: the five discovery agents behind ``DiscoveryAgentsPort``, run through the agent factory."""

from __future__ import annotations

from pydantic import BaseModel

from scout.adapters.llm.factory import AgentFactory
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
from scout.discovery.ports.agents import AgentOutcome
from scout.ports.llm import AgentSpec, CallContext


class AgentsDiscovery:
    """One spec per agent, loaded at construction so a broken prompt fails at startup."""

    def __init__(self, factory: AgentFactory) -> None:
        self._f = factory
        self._planner = factory.spec("QueryPlanner", QueryPlan)
        self._labeler = factory.spec("ClusterLabeler", ClusterLabel)
        self._tagger = factory.spec("InsightTopicTagger", InsightTopicTags)
        self._analyst = factory.spec("GapAnalyst", GapAnalysis)
        self._critic = factory.spec("Critic", CriticVerdict)

    async def _run[T: BaseModel](
        self, spec: AgentSpec[T], data: BaseModel, ctx: CallContext
    ) -> AgentOutcome[T]:
        r = await self._f.run(spec, data, ctx)
        return AgentOutcome(r.output, AgentFactory.version(spec), r.model, r.cost_usd, r.usage)

    async def plan_queries(self, data: QueryPlannerInput, ctx: CallContext) -> AgentOutcome[QueryPlan]:
        """QueryPlanner."""
        return await self._run(self._planner, data, ctx)

    async def label_cluster(self, data: ClusterLabelInput, ctx: CallContext) -> AgentOutcome[ClusterLabel]:
        """ClusterLabeler."""
        return await self._run(self._labeler, data, ctx)

    async def tag_insights(
        self, data: InsightTopicTaggerInput, ctx: CallContext
    ) -> AgentOutcome[InsightTopicTags]:
        """InsightTopicTagger."""
        return await self._run(self._tagger, data, ctx)

    async def analyse(self, data: GapContext, ctx: CallContext) -> AgentOutcome[GapAnalysis]:
        """GapAnalyst."""
        return await self._run(self._analyst, data, ctx)

    async def critique(self, data: CriticInput, ctx: CallContext) -> AgentOutcome[CriticVerdict]:
        """Critic."""
        return await self._run(self._critic, data, ctx)
