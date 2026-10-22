"""ADR 016, provenance: text derived from confidential input is itself confidential.

A spy backend sits behind the real agent stack (``AgentsDiscovery`` over ``AgentFactory``). It models an
installation where only some tiers have authorized deployments: a call reaches an *unauthorized* deployment when it
is sent as published (unpinned route) or when its tier has none. No vault string and nothing derived from it
(the analyst's drafts, earlier opportunity titles) may ever be in such a call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from tests.fakes import FakeLLM, InMemoryUsageMeter
from tests.unit.discovery._world import World, after_step, analyst, critic, plan

from scout.adapters.llm.factory import AgentFactory
from scout.adapters.llm.fake_llm import canonical_json
from scout.discovery.adapters.agents import AgentsDiscovery
from scout.discovery.domain.agent_io import ClusterLabel, CriticInput, GapAnalysis, GapContext
from scout.discovery.domain.enums import OppStatus, RunStatus, RunStep
from scout.ports.llm import DataClass, ModelTier
from scout.ports.usage import ModelPrice, PriceTable
from scout.shared import FixedClock, SequentialIdGenerator

PROMPTS = Path(__file__).resolve().parents[4] / "prompts"
VAULT = ("Develop lignin foams", "insulation panels")  # insight and passage text of the sample document
DERIVED = "VAULTDERIVED"  # marks what the analyst wrote while it saw the vault


class TierSpy(FakeLLM):
    """Per-tier authorization; every call is recorded as (agent, class it was sent as, tier, payload)."""

    def __init__(self, authorized: set[ModelTier]) -> None:
        super().__init__(
            Path("/nonexistent"), InMemoryUsageMeter(),
            PriceTable(version="t", models={"fake": ModelPrice(input=Decimal(1), output=Decimal(2))}),
            FixedClock(datetime(2026, 1, 1, tzinfo=UTC)), SequentialIdGenerator(),
        )  # fmt: skip
        self.authorized = authorized
        self.calls: list[tuple[str, DataClass, ModelTier, str]] = []

    def confidential_allowed(self, tier: ModelTier | None = None) -> bool:
        return all(t in self.authorized for t in ([tier] if tier else list(ModelTier)))

    async def run(self, agent: Any, input: Any, ctx: Any) -> Any:
        self.calls.append((agent.name, agent.data_class, agent.tier, canonical_json(input)))
        return await super().run(agent, input, ctx)

    def unauthorized_deliveries(self) -> list[tuple[str, str]]:
        """(agent, marker) for every call that a non-authorized deployment would have received with a secret."""
        out = []
        for name, klass, tier, payload in self.calls:
            reaches_unauthorized = klass is DataClass.PUBLISHED or tier not in self.authorized
            out += [(name, s) for s in (*VAULT, DERIVED) if reaches_unauthorized and s in payload]
        return out

    def with_secrets(self) -> list[tuple[str, DataClass]]:
        return [(n, k) for n, k, _, p in self.calls if any(s in p for s in (*VAULT, DERIVED))]


def marked_analyst(data: GapContext) -> GapAnalysis:
    """The default script; when the analyst saw the vault its titles and claims carry the marker."""
    out = analyst(data)
    if not (data.insights or data.chunks):
        return _plain(out)  # published-only call: it could only cite works and counts
    for o in out.opportunities:
        o.title = f"{DERIVED} {o.title}"
        for c in o.claims:
            c.text = f"{DERIVED} {c.text}"
    return out


def stack(authorized: set[ModelTier]) -> tuple[World, TierSpy]:
    spy = TierSpy(authorized)
    spy.script("QueryPlanner", plan)
    spy.script("GapAnalyst", marked_analyst)
    spy.script("Critic", critic)
    spy.script(
        "ClusterLabeler",
        lambda d: ClusterLabel(cluster_id=d.cluster_id, label="L " + d.cluster_id, search_terms=["a", "b"]),
    )
    w = World()
    w.agents = AgentsDiscovery(AgentFactory(spy, PROMPTS))  # type: ignore[assignment]
    return w, spy


async def test_fully_authorized_run_sends_everything_derived_as_confidential() -> None:
    w, spy = stack(set(ModelTier))
    run = await w.start()
    await w.run(run)
    assert w.get(run).status is RunStatus.DONE and w.get(run).inputs.basis == "full"
    assert w.shown(run)
    assert spy.unauthorized_deliveries() == []
    sent = {(n, k) for n, k in spy.with_secrets()}
    # the analyst saw the vault, and the Critic saw the analyst's draft: both pinned
    assert ("GapAnalyst", DataClass.CONFIDENTIAL) in sent and ("Critic", DataClass.CONFIDENTIAL) in sent


async def test_partly_authorized_installation_runs_on_published_data_and_leaks_nothing() -> None:
    w, spy = stack({ModelTier.FAST})  # reasoning has no authorized deployment
    run = await w.start()
    await w.run(run)
    assert w.get(run).status is RunStatus.DONE and w.get(run).inputs.basis == "published_only"
    assert spy.calls and spy.unauthorized_deliveries() == [] and spy.with_secrets() == []


async def test_earlier_titles_from_a_full_run_never_reach_a_published_only_run() -> None:
    w, spy = stack(set(ModelTier))
    first = await w.start()
    await w.run(first)
    shown = [o.title for o in w.shown(first)]
    assert shown and all(DERIVED in t for t in shown)
    spy.authorized = {ModelTier.FAST}  # authorization is partly withdrawn: the next run is published only
    spy.calls.clear()

    second = await w.start()
    await w.run(second)
    assert w.get(second).inputs.basis == "published_only"
    assert spy.calls and spy.unauthorized_deliveries() == []
    assert not any(t in p for _, _, _, p in spy.calls for t in shown)  # not even as an exclude list


async def test_published_only_run_still_excludes_titles_of_published_only_runs() -> None:
    w, spy = stack({ModelTier.FAST})
    spy.script("GapAnalyst", lambda d: _plain(analyst(d)))
    first = await w.start()
    await w.run(first)
    titles = [o.title for o in w.shown(first)]
    assert titles
    spy.calls.clear()
    second = await w.start()
    await w.run(second)
    exclude = [p for n, _, _, p in spy.calls if n == "GapAnalyst"]
    assert exclude and all(t in exclude[0] for t in titles)


def _plain(out: GapAnalysis) -> GapAnalysis:
    for o in out.opportunities:
        o.claims[0].evidence_refs = [r for r in o.claims[0].evidence_refs if r.type != "chunk"]
    return out


async def test_withdrawing_authorization_mid_run_stops_the_critic_from_seeing_the_drafts() -> None:
    w, spy = stack(set(ModelTier))
    run = await w.start()

    def withdraw(_: object) -> None:
        spy.authorized = set()
        spy.calls.clear()  # what was sent while authorized was legitimate; watch what follows

    after_step(w, RunStep.ANALYSE, withdraw)
    await w.run(run, last=True)
    assert w.opps(run)  # the drafts exist
    assert not any(n == "Critic" for n, *_ in spy.calls)  # the drafts are confidential: not sent anywhere
    assert spy.unauthorized_deliveries() == []
    assert not w.shown(run)
    assert all(o.status is not OppStatus.SHOWN for o in w.opps(run))


def test_the_critic_input_is_confidential_exactly_when_the_run_is_tainted() -> None:
    from scout.discovery.domain.agent_io import CriticClaim, CriticDraft
    from scout.shared.confidential import contains_confidential, input_for_route

    draft = CriticDraft(
        title="T", kind="white_spot", claims=[CriticClaim(index=0, text="c", check_status="passed")]
    )
    clean, tainted = (
        CriticInput(draft=draft, evidence_texts=[]),
        CriticInput(draft=draft, evidence_texts=[], tainted=True),
    )
    assert not contains_confidential(clean) and contains_confidential(tainted)
    assert input_for_route(DataClass.CONFIDENTIAL, tainted, authorized=False) is None  # no published form
    _, klass = input_for_route(DataClass.CONFIDENTIAL, tainted, authorized=True)  # type: ignore[misc]
    assert klass is DataClass.CONFIDENTIAL
    assert "tainted" not in canonical_json(tainted)  # not part of the prompt input
