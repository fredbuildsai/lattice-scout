"""A spy backend never receives confidential strings in unauthorized mode (ADR 016)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.fakes import FakeLLM, InMemoryUsageMeter

from scout.adapters.llm.factory import AgentFactory
from scout.adapters.llm.fake_llm import canonical_json
from scout.discovery.adapters.agents import AgentsDiscovery
from scout.discovery.domain.agent_io import (
    ChunkForAnalyst,
    GapAnalysis,
    GapContext,
    InsightForAnalyst,
    InsightTag,
    InsightTopicTaggerInput,
    InsightTopicTags,
    ProfileSummary,
    TagCluster,
    TagInsight,
)
from scout.ports.llm import CallContext, ConfidentialDataRefused
from scout.ports.usage import PriceTable
from scout.shared import FixedClock, SequentialIdGenerator

SECRET = "SECRET-UNPUBLISHED-TEXT"
PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


class SpyLLM(FakeLLM):
    """Records every canonical input the backend receives and the data class it was sent as."""

    def __init__(self) -> None:
        super().__init__(
            Path("/nonexistent"), InMemoryUsageMeter(), PriceTable(version="t", models={}),
            FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator(),
        )  # fmt: skip
        self.received: list[tuple[str, str, str]] = []

    async def run(self, agent: Any, input: Any, ctx: CallContext) -> Any:
        self.received.append((agent.name, agent.data_class.value, canonical_json(input)))
        return await super().run(agent, input, ctx)


def _context() -> GapContext:
    return GapContext(
        profile_summary=ProfileSummary(topics=["lignin"], recent_works=[]),
        insights=[InsightForAnalyst(insight_ref="d1.aims[0]", document="d1", section="aims", text=SECRET)],
        coverage=[], novelty=[], papers=[],
        chunks=[ChunkForAnalyst(chunk_id="d1.c1", document="d1", page=1, text=SECRET)],
    )  # fmt: skip


def _agents(authorized: bool) -> tuple[AgentsDiscovery, SpyLLM]:
    spy = SpyLLM()
    spy.confidential_ok = authorized
    spy.script("GapAnalyst", GapAnalysis(opportunities=[]))
    spy.script("InsightTopicTagger", InsightTopicTags(tags=[InsightTag(insight_ref="r")]))
    return AgentsDiscovery(AgentFactory(spy, PROMPTS)), spy


async def test_unauthorized_backend_never_receives_the_confidential_strings() -> None:
    agents, spy = _agents(authorized=False)
    await agents.analyse(_context(), CallContext(feature="f"))
    assert len(spy.received) == 1
    name, klass, payload = spy.received[0]
    assert name == "GapAnalyst" and klass == "published"  # sent as published: the confidential parts are gone
    assert SECRET not in payload and "lignin" in payload  # the published remainder still goes


async def test_authorized_backend_receives_everything_as_confidential() -> None:
    agents, spy = _agents(authorized=True)
    await agents.analyse(_context(), CallContext(feature="f"))
    _, klass, payload = spy.received[0]
    assert klass == "confidential" and SECRET in payload


async def test_a_wholly_confidential_step_is_not_sent_at_all_when_unauthorized() -> None:
    agents, spy = _agents(authorized=False)
    data = InsightTopicTaggerInput(
        clusters=[TagCluster(cluster_id="k1", label="L")], insights=[TagInsight(insight_ref="r", text=SECRET)]
    )
    with pytest.raises(ConfidentialDataRefused):
        await agents.tag_insights(data, CallContext(feature="f"))
    assert spy.received == []


async def test_even_a_raw_mapping_is_not_sent_when_unauthorized() -> None:
    spy = SpyLLM()
    spy.confidential_ok = False
    factory = AgentFactory(spy, PROMPTS)
    spec = factory.spec("GapAnalyst", GapAnalysis)
    with pytest.raises(ConfidentialDataRefused):
        await factory.run(spec, {"text": SECRET}, CallContext(feature="f"))
    assert spy.received == []
