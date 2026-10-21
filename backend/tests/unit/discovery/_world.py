"""A complete in-memory world for pipeline tests: profile, vault, literature, scripted agents, the run job."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from tests.fakes.discovery import (
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
from scout.discovery.domain.field import FieldConfig, NoveltyBands, NoveltyConfig
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.ports.sources import DocMaterial, InsightView, PassageView, ProfileWork, RunProfile
from scout.ports.literature import TopicRef, WorkQuery, WorkRecord
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
    """Search returns the same works for every query; counts come from a dict (default 120)."""

    def __init__(self) -> None:
        self.works = [work(i, "T2" if i <= 5 else "T3" if i <= 9 else "T1") for i in range(1, 13)]
        self.counts: dict[str, int] = {}
        self.calls: list[str] = []
        self.known = {w.id: w for w in self.works}

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        self.calls.append("search")
        return self.works[: query.per_page]

    async def count_works(self, query: WorkQuery) -> int:
        self.calls.append("count")
        return self.counts.get(query.search, 120)

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        self.calls.append("get")
        return self.known.get(work_id_or_doi)


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
            self.uow, self.ledger, self.literature, self.agents, self.profiles, self.vault, FIELD, self.cfg, self.clock
        )  # fmt: skip

    @property
    def sweeper(self) -> SweepRuns:
        return SweepRuns(self.uow, self.jobs, self.clock)

    async def start(self, docs: tuple[UUID, ...] = (DOC_ID,)) -> AnalysisRun:
        return await self.starter.start(OWNER, docs)

    async def run(self, run: AnalysisRun, *, last: bool = False) -> dict[str, Any]:
        return await self.runner.handle({"run_id": str(run.id)}, last_attempt=last)  # type: ignore[return-value]

    def get(self, run: AnalysisRun) -> AnalysisRun:
        return self.store.runs[run.id]


ZERO = Decimal(0)
