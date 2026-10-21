"""ADR 016: with no authorized endpoint the run goes ahead on published data only, says so, and leaks nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.fakes import FakeLLM, InMemoryUsageMeter
from tests.unit.discovery._world import (
    DOC_ID,
    OWNER,
    World,
    analyst,
    critic,
    plan,
)

from scout.adapters.llm.factory import AgentFactory
from scout.adapters.llm.fake_llm import canonical_json
from scout.discovery.adapters.agents import AgentsDiscovery
from scout.discovery.domain.agent_io import ClusterLabel, GapContext
from scout.discovery.domain.enums import OppStatus, RunStatus
from scout.ports.usage import ModelPrice, PriceTable
from scout.shared import FixedClock, SequentialIdGenerator

VAULT_STRINGS = (
    "Develop lignin foams",
    "insulation panels",
)  # insight and passage text of the sample document
PROMPTS = Path(__file__).resolve().parents[4] / "prompts"


def published_analyst(data: GapContext) -> Any:
    """Like the default script, but the model can only cite what it was given: works and counts."""
    assert data.insights == [] and data.chunks == []
    out = analyst(data)
    for o in out.opportunities:
        for c in o.claims:
            c.evidence_refs = [r for r in c.evidence_refs if r.type != "chunk"]
    return out


async def test_fake_agents_run_completes_on_published_data_and_is_marked() -> None:
    w = World()
    w.agents.allowed = False
    w.agents.script("GapAnalyst", published_analyst)
    run = await w.start()
    await w.run(run)
    final = w.get(run)
    assert final.status is RunStatus.DONE and final.inputs.basis == "published_only"
    shown = [o for o in w.opps(run) if o.status is OppStatus.SHOWN]
    assert shown and all(o.score_basis["basis"] == "published_only" for o in shown)
    for o in shown:  # the evidence rule still holds: at least two verified items, none from the vault
        items = {
            (e.source_type.value, e.ref)
            for c in o.visible_claims()
            if c.section.value != "score"
            for e in c.evidence
        }
        assert len(items) >= 2 and all(t in ("literature", "count") for t, _ in items)
    for _, data in w.agents.calls:  # nothing from the vault reached any agent input
        assert not any(v in canonical_json(data) for v in VAULT_STRINGS)
    assert w.agents.count("InsightTopicTagger") == 0


async def test_an_authorized_installation_still_uses_the_vault() -> None:
    w = World()
    run = await w.start()
    await w.run(run)
    assert w.get(run).inputs.basis == "full"
    assert any(v in canonical_json(d) for _, d in w.agents.calls for v in VAULT_STRINGS)


async def test_real_agent_stack_over_a_spy_backend_never_sees_vault_strings() -> None:
    spy_inputs: list[str] = []

    class Spy(FakeLLM):
        async def run(self, agent: Any, input: Any, ctx: Any) -> Any:
            spy_inputs.append(canonical_json(input))
            return await super().run(agent, input, ctx)

    prices = PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))})
    llm = Spy(
        Path("/nonexistent"),
        InMemoryUsageMeter(),
        prices,
        FixedClock(datetime(2026, 1, 1, tzinfo=UTC)),
        SequentialIdGenerator(),
    )
    llm.confidential_ok = False
    llm.script("QueryPlanner", plan)
    llm.script("GapAnalyst", published_analyst)
    llm.script("Critic", critic)
    llm.script(
        "ClusterLabeler",
        lambda d: ClusterLabel(cluster_id=d.cluster_id, label="L " + d.cluster_id, search_terms=["a", "b"]),
    )
    w = World()
    w.agents = AgentsDiscovery(AgentFactory(llm, PROMPTS))  # type: ignore[assignment]
    run = await w.start()
    await w.run(run)
    assert w.get(run).status is RunStatus.DONE and w.get(run).inputs.basis == "published_only"
    assert spy_inputs and not any(v in i for i in spy_inputs for v in VAULT_STRINGS)
    assert OWNER and DOC_ID
