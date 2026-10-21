"""A complete in-memory world for pipeline tests: profile, vault, literature, scripted agents, the run job."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from tests.fakes.discovery import (
    AgentKilled,
    DiscoveryStore,
    FakeDiscoveryAgents,
    FakeProfileSource,
    FakeVaultSource,
    InMemoryDiscoveryUoW,
    InMemoryLedger,
)
from tests.fakes.jobs import InMemoryJobQueue

from scout.discovery.application.pipeline import PipelineConfig
from scout.discovery.application.run_analysis import RunAnalysis, SweepRuns
from scout.discovery.application.start import StartAnalysisRun, StartConfig
from scout.discovery.domain.agent_io import (
    ClaimDraft,
    ClaimVerdict,
    CriticVerdict,
    EvidenceRefOut,
    GapAnalysis,
    GapContext,
    OpportunityDraft,
    PlannedQuery,
    QueryPlan,
)
from scout.discovery.domain.enums import ClaimStatus, OppStatus, RunStep
from scout.discovery.domain.field import FieldConfig, NoveltyBands, NoveltyConfig
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.ports.sources import DocMaterial, InsightView, PassageView, ProfileWork, RunProfile
from scout.ports.literature import LiteraturePort, TopicRef, WorkQuery, WorkRecord
from scout.shared import AccountId, FixedClock, SequentialIdGenerator

OWNER = AccountId(UUID(int=11))
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
DOC_ID = UUID(int=500)
CHUNK_ID = UUID(int=501)
ABSTRACTS = {
    f"W{i}": f"Study {i} reports that lignin based foams reach lower thermal conductivity than polystyrene panels."
    for i in range(1, 13)
}
FIELD = FieldConfig(
    key="f", label="Bio-based materials", openalex_topic_ids=["T1"], keywords=["lignin"],
    novelty=NoveltyConfig(bands=NoveltyBands(very_thin=50, thin=300, moderate=1500)),
)  # fmt: skip


def topic(tid: str, sub: str = "Materials") -> TopicRef:
    return TopicRef(id=tid, name=f"Topic {tid}", subfield=sub)


def work(i: int, tid: str, *, abstract: str | None = None) -> WorkRecord:
    return WorkRecord(
        id=f"W{i}", title=f"Lignin foam study {i}", year=2024, cited_by_count=i, type="article",
        abstract=ABSTRACTS.get(f"W{i}") if abstract is None else abstract, topics=(topic(tid),),
        author_names=("A. Author",),
    )  # fmt: skip


class ScriptedLiterature:
    """Search returns the same works for every query; counts come from a dict (default 120).

    ``kill_on=("search", 5)`` raises ``AgentKilled`` on the 5th call of that kind (the call is logged first);
    ``fail={"count": exc}`` raises ``exc`` on every call of that kind.
    """

    def __init__(self) -> None:
        self.works = [work(i, "T2" if i <= 5 else "T3" if i <= 9 else "T1") for i in range(1, 13)]
        self.counts: dict[str, int] = {}
        self.calls: list[str] = []
        self.search_log: list[str] = []
        self.known = {w.id: w for w in self.works}
        self.kill_on: tuple[str, int] | None = None
        self.fail: dict[str, Exception] = {}

    def _enter(self, kind: str) -> None:
        self.calls.append(kind)
        if self.kill_on == (kind, self.calls.count(kind)):
            raise AgentKilled
        if kind in self.fail:
            raise self.fail[kind]

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        self.search_log.append(query.search)  # logged before a kill, like the call itself
        self._enter("search")
        return self.works[: query.per_page]

    async def count_works(self, query: WorkQuery) -> int:
        self._enter("count")
        return self.counts.get(query.search, 120)

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        self._enter("get")
        return self.known.get(work_id_or_doi)


def plan_n(n: int) -> Callable[[Any], QueryPlan]:
    """A QueryPlanner script with ``n`` distinct queries (the second one is the ``objection`` query)."""

    def make(_: Any) -> QueryPlan:
        return QueryPlan(
            queries=[
                PlannedQuery(
                    search=f"lignin foam topic {i}", purpose="objection" if i == 1 else "core", max_results=50
                )
                for i in range(n)
            ]
        )

    return make


def plan(_: Any) -> QueryPlan:
    return QueryPlan(
        queries=[
            PlannedQuery(search="lignin foam insulation", purpose="core", max_results=50),
            PlannedQuery(search="lignin foam fire safety limits", purpose="objection", max_results=50),
        ]
    )


def analyst(data: GapContext) -> GapAnalysis:
    """Two opportunities per call, each citing a corpus work with a verbatim quote, a passage and a count."""
    paper = data.papers[0]
    quote = paper.abstract[:60]
    n = data.novelty[0].candidate_id
    drafts = []
    for k in range(2):
        drafts.append(
            OpportunityDraft(
                kind="white_spot",
                title=f"Distinct opportunity {data.coverage[0].cluster_id} {k} {'x' * k}{paper.work_id}",
                one_line="A gap",
                cluster_ids=[data.coverage[0].cluster_id],
                claims=[
                    ClaimDraft(
                        text=f"Reported work {paper.work_id} supports the direction {k}.",
                        section="rationale",
                        evidence_refs=[
                            EvidenceRefOut(type="work", id=paper.work_id, quote=quote),
                            EvidenceRefOut(
                                type="chunk", id="d1.c1", quote="develop lignin foams for insulation"
                            ),
                        ],
                    ),
                    ClaimDraft(
                        text="Measured literature volume is small.",
                        section="rationale",
                        evidence_refs=[EvidenceRefOut(type="count", id=n)],
                    ),
                ],
                effort_estimate="medium",
            )  # fmt: skip
        )
    return GapAnalysis(opportunities=drafts)


def critic(data: Any) -> CriticVerdict:
    return CriticVerdict(
        claims=[ClaimVerdict(index=c.index, status="verified") for c in data.draft.claims], keep=True
    )


@dataclass
class World:
    """Everything wired like the container does it."""

    store: DiscoveryStore = field(default_factory=DiscoveryStore)
    clock: FixedClock = field(default_factory=lambda: FixedClock(NOW))
    ids: SequentialIdGenerator = field(default_factory=SequentialIdGenerator)
    agents: FakeDiscoveryAgents = field(default_factory=FakeDiscoveryAgents)
    literature: ScriptedLiterature = field(default_factory=ScriptedLiterature)
    profiles: FakeProfileSource = field(default_factory=FakeProfileSource)
    vault: FakeVaultSource = field(default_factory=FakeVaultSource)
    cfg: PipelineConfig = field(default_factory=PipelineConfig)
    start_cfg: StartConfig = field(default_factory=StartConfig)

    def __post_init__(self) -> None:
        self.ledger = InMemoryLedger(self.store, self.clock)
        self.jobs = InMemoryJobQueue(self.clock, self.ids)
        self.profiles.profiles[OWNER] = RunProfile(
            researcher_id=UUID(int=77), topics=(("T1", "Topic T1", "Materials", 0.8),),
            works=tuple(
                ProfileWork(f"W10{i}", f"My lignin paper {i}", "About lignin.", 2023, 3, "article", None, (("T1", "Topic T1", "Materials"),))
                for i in range(2)
            ),
            import_done=True,
        )  # fmt: skip
        self.vault.add(
            OWNER,
            DocMaterial(
                DOC_ID, "d1", "note.pdf", "keep", True,
                insights=(InsightView("d1.aims[0]", "aims", "Develop lignin foams", ("d1.c1",), 1, 1),),
                passages=(PassageView("d1.c1", CHUNK_ID, DOC_ID, 1, 1, "keep", "We aim to develop lignin foams for insulation panels."),),
                keywords=("lignin",),
            ),
        )  # fmt: skip
        self.agents.script("QueryPlanner", plan)
        self.agents.script("GapAnalyst", analyst)
        self.agents.script("Critic", critic)

    def uow(self) -> InMemoryDiscoveryUoW:
        return InMemoryDiscoveryUoW(self.store)

    @property
    def starter(self) -> StartAnalysisRun:
        return StartAnalysisRun(
            self.uow, self.jobs, self.profiles, self.vault, self.clock, self.ids, self.start_cfg
        )

    @property
    def runner(self) -> RunAnalysis:
        return RunAnalysis(
            self.uow, self.ledger, cast(LiteraturePort, self.literature), self.agents, self.profiles, self.vault,
            FIELD, self.cfg, self.clock,
        )  # fmt: skip

    @property
    def sweeper(self) -> SweepRuns:
        return SweepRuns(self.uow, self.jobs, self.clock)

    async def start(self, docs: tuple[UUID, ...] = (DOC_ID,)) -> AnalysisRun:
        return await self.starter.start(OWNER, docs)

    async def run(self, run: AnalysisRun, *, last: bool = False) -> dict[str, Any]:
        return dict(await self.runner.handle({"run_id": str(run.id)}, last_attempt=last))

    def get(self, run: AnalysisRun) -> AnalysisRun:
        return self.store.runs[run.id]

    def set_budget(self, run: AnalysisRun, **changes: Any) -> None:
        """Replace fields of the stored run's budget (a run cannot be *started* with a budget this small)."""
        stored = self.store.runs[run.id]
        stored.budget = dataclasses.replace(stored.budget, **changes)

    def opps(self, run: AnalysisRun) -> list[Any]:
        """The run's opportunities in (batch, ord) order."""
        return sorted(
            (o for o in self.store.opportunities.values() if o.run_id == run.id),
            key=lambda o: (o.batch, o.ord),
        )

    def shown(self, run: AnalysisRun) -> list[Any]:
        """Opportunities with status ``shown``."""
        return [o for o in self.opps(run) if o.status is OppStatus.SHOWN]


ZERO = Decimal(0)
STEP_CALLS: dict[RunStep, frozenset[str]] = {
    RunStep.PLAN_QUERIES: frozenset({"QueryPlanner"}),
    RunStep.RETRIEVE: frozenset({"search"}),
    RunStep.GROUP_BY_TOPIC: frozenset({"ClusterLabeler"}),
    RunStep.COVERAGE: frozenset({"InsightTopicTagger"}),
    RunStep.MEASURE_NOVELTY: frozenset({"count"}),
    RunStep.ANALYSE: frozenset({"GapAnalyst"}),
    RunStep.VERIFY: frozenset({"Critic", "get"}),
    RunStep.SCORE_PERSIST: frozenset(),
}  # which agent / literature calls belong to which step
STEPS = list(RunStep)


def src_bug(number: int, why: str) -> Any:
    """Mark a test that exposes a defect in ``backend/src``. Strict: it turns red as soon as the defect is fixed."""
    return pytest.mark.xfail(strict=True, reason=f"SRC BUG {number}: {why}")


def call_names(w: World, start: int = 0, lit_start: int = 0) -> list[str]:
    """Names of the agent and literature calls made so far (from the given offsets), agents first."""
    return [a for a, _ in w.agents.calls[start:]] + w.literature.calls[lit_start:]


def fingerprint(store: DiscoveryStore, run_id: UUID) -> dict[str, Any]:
    """Everything a run persisted, minus the things that legitimately differ after a resume (counters, spend)."""
    run = dataclasses.asdict(store.runs[run_id])
    for key in ("generation", "executions", "spend", "heartbeat_at", "updated_at"):
        run.pop(key)
    return {
        "run": run,
        "corpus": {k: dataclasses.asdict(v) for k, v in store.corpus.get(run_id, {}).items()},
        "clusters": {c.key: dataclasses.asdict(c) for c in store.clusters.get(run_id, {}).values()},
        "probes": {p.label: dataclasses.asdict(p) for p in store.probes.get(run_id, {}).values()},
        "opportunities": {
            str(o.id): dataclasses.asdict(o) for o in store.opportunities.values() if o.run_id == run_id
        },
    }


def assert_user_view_clean(store: DiscoveryStore) -> None:
    """The user-facing invariants, over every opportunity in the store.

    Only ``shown|saved|dismissed`` opportunities may be user-visible and each must pass the show rule; whatever
    ``visible_claims`` returns is verified, with verified evidence only.
    """
    for o in store.opportunities.values():
        for c in o.visible_claims():
            assert c.status is ClaimStatus.VERIFIED
            assert all(e.verified is True for e in c.evidence)
        if o.status in (OppStatus.SHOWN, OppStatus.SAVED, OppStatus.DISMISSED):
            assert o.show_decision()[0], (o.title, o.show_decision())
            assert o.scores is not None and o.scores.novelty is not None


def row_ids(store: DiscoveryStore, run_id: UUID) -> dict[str, set[Any]]:
    """The identities of every row the run persisted (a resume must neither lose nor duplicate any)."""
    opps = [o for o in store.opportunities.values() if o.run_id == run_id]
    return {
        "corpus": {w.id for w in store.corpus.get(run_id, {}).values()},
        "clusters": {c.id for c in store.clusters.get(run_id, {}).values()},
        "probes": {p.id for p in store.probes.get(run_id, {}).values()},
        "opportunities": {o.id for o in opps},
        "claims": {c.id for o in opps for c in o.claims},
        "evidence": {e.id for o in opps for c in o.claims for e in c.evidence},
    }


async def finished_world(setup: Callable[[World], None] | None = None) -> tuple[World, AnalysisRun]:
    """A world whose run went through uninterrupted (the reference a resumed run is compared with)."""
    w = World()
    if setup is not None:
        setup(w)
    run = await w.start()
    await w.run(run)
    return w, run


def kill_at_boundary(w: World, step: RunStep) -> None:
    """Crash when the commit that completes ``step`` is about to be published (output committed, step not).

    Fires once; the lost commit leaves the run at ``step`` with all its outputs already persisted.
    """
    fired: list[bool] = []

    def before(staged: dict[UUID, AnalysisRun]) -> None:
        if fired:
            return
        for rid, r in staged.items():
            rec = r.step_state.get(step)
            old = w.store.runs.get(rid)
            was = old.step_state.get(step) if old else None
            if rec is not None and rec.finished_at is not None and (was is None or was.finished_at is None):
                fired.append(True)
                raise AgentKilled

    w.store.before_commit = before


def after_step(w: World, step: RunStep, action: Callable[[AnalysisRun], None]) -> None:
    """Call ``action(stored_run)`` once, right after the commit that completes ``step`` is published."""
    fired: list[bool] = []

    def after() -> None:
        if fired:
            return
        for r in w.store.runs.values():
            rec = r.step_state.get(step)
            if rec is not None and rec.finished_at is not None:
                fired.append(True)
                action(r)

    w.store.after_commit = after
