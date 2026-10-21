"""``AgentsMatching``: the three matching agents behind ``MatchAgentsPort``, run through the agent factory."""

from __future__ import annotations

from pydantic import BaseModel

from scout.adapters.llm.factory import AgentFactory
from scout.matching.domain.agent_io import (
    JointIdeaList,
    JointIdeasInput,
    MatchExplainerInput,
    MatchRationale,
    MethodExtractorInput,
    MethodSet,
)
from scout.ports.llm import AgentOutcome, AgentSpec, CallContext


class AgentsMatching:
    """One spec per agent, loaded at construction so a broken prompt fails at startup."""

    def __init__(self, factory: AgentFactory) -> None:
        self._f = factory
        self._methods = factory.spec("MethodExtractor", MethodSet)
        self._explainer = factory.spec("MatchExplainer", MatchRationale)
        self._ideas = factory.spec("JointIdeas", JointIdeaList)

    async def _run[T: BaseModel](
        self, spec: AgentSpec[T], data: BaseModel, ctx: CallContext
    ) -> AgentOutcome[T]:
        r = await self._f.run(spec, data, ctx)
        return AgentOutcome(r.output, AgentFactory.version(spec), r.model, r.cost_usd, r.usage)

    async def extract_methods(self, data: MethodExtractorInput, ctx: CallContext) -> AgentOutcome[MethodSet]:
        """MethodExtractor."""
        return await self._run(self._methods, data, ctx)

    async def explain(self, data: MatchExplainerInput, ctx: CallContext) -> AgentOutcome[MatchRationale]:
        """MatchExplainer."""
        return await self._run(self._explainer, data, ctx)

    async def joint_ideas(self, data: JointIdeasInput, ctx: CallContext) -> AgentOutcome[JointIdeaList]:
        """JointIdeas."""
        return await self._run(self._ideas, data, ctx)
