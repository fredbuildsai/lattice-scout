"""In-memory discovery repositories, ledger, sources and scripted agents (staged copies; ``commit`` publishes)."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self
from uuid import UUID

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
from scout.discovery.domain.budget import RunSpend
from scout.discovery.domain.corpus import Cluster, CorpusWork, NoveltyProbe
from scout.discovery.domain.enums import CorpusOrigin, RunStatus
from scout.discovery.domain.opportunity import Opportunity
from scout.discovery.domain.run import AnalysisRun
from scout.discovery.ports.agents import AgentOutcome
from scout.discovery.ports.ledger import LedgerState
from scout.discovery.ports.repos import RunInProgress, StaleRuns
from scout.discovery.ports.sources import DocMaterial, DocumentCheck, PassageView, RunProfile
from scout.ports.llm import CallContext, TokenUsage
from scout.shared import AccountId, Clock
from tests.fakes.audit import InMemoryAuditLog


@dataclass
class DiscoveryStore:
    """Committed state."""

    runs: dict[UUID, AnalysisRun] = field(default_factory=dict)
    corpus: dict[UUID, dict[str, CorpusWork]] = field(default_factory=dict)  # run -> openalex id -> work
    clusters: dict[UUID, dict[UUID, Cluster]] = field(default_factory=dict)
    probes: dict[UUID, dict[UUID, NoveltyProbe]] = field(default_factory=dict)
    opportunities: dict[UUID, Opportunity] = field(default_factory=dict)
    audit: InMemoryAuditLog = field(default_factory=InMemoryAuditLog)
    commits: int = 0


class InMemoryRunRepo:
    def __init__(self, data: dict[UUID, AnalysisRun]) -> None:
        self.data = data

    async def get(self, run_id: UUID, owner_id: AccountId) -> AnalysisRun | None:
        r = self.data.get(run_id)
        return copy.deepcopy(r) if r is not None and r.owner_id == owner_id else None

    async def get_for_update(self, run_id: UUID) -> AnalysisRun | None:
        return copy.deepcopy(self.data.get(run_id))

    async def add(self, run: AnalysisRun) -> None:
        if run.active and await self.active_for(run.owner_id) is not None:
            raise RunInProgress("active run")
        self.data[run.id] = copy.deepcopy(run)

    async def save(self, run: AnalysisRun) -> None:
        self.data[run.id] = copy.deepcopy(run)

    async def active_for(self, owner_id: AccountId) -> AnalysisRun | None:
        return next(
            (copy.deepcopy(r) for r in self.data.values() if r.owner_id == owner_id and r.active), None
        )

    async def count_started_since(self, owner_id: AccountId, since: datetime) -> int:
        return sum(1 for r in self.data.values() if r.owner_id == owner_id and r.created_at >= since)

    async def spend_since(self, owner_id: AccountId, since: datetime) -> Decimal:
        return sum(
            (
                r.spend.cost_usd
                for r in self.data.values()
                if r.owner_id == owner_id and r.created_at >= since
            ),
            Decimal(0),
        )

    async def list_for(self, owner_id: AccountId, limit: int) -> list[AnalysisRun]:
        runs = sorted(
            (r for r in self.data.values() if r.owner_id == owner_id),
            key=lambda r: r.created_at,
            reverse=True,
        )
        return [copy.deepcopy(r) for r in runs[:limit]]

    async def list_stale(
        self, queued_before: datetime, heartbeat_before: datetime, max_executions: int, limit: int
    ) -> StaleRuns:
        queued = [
            r.id for r in self.data.values() if r.status is RunStatus.QUEUED and r.created_at < queued_before
        ]
        running = [
            r for r in self.data.values()
            if r.status is RunStatus.RUNNING and (r.heartbeat_at or r.created_at) < heartbeat_before
        ]  # fmt: skip
        return StaleRuns(
            queued=tuple(queued[:limit]),
            stalled=tuple(r.id for r in running if r.executions < max_executions)[:limit],
            exhausted=tuple(r.id for r in running if r.executions >= max_executions)[:limit],
        )


class InMemoryCorpusRepo:
    def __init__(self, data: dict[UUID, dict[str, CorpusWork]]) -> None:
        self.data = data

    async def upsert_many(self, works: Sequence[CorpusWork]) -> None:
        for w in works:
            bucket = self.data.setdefault(w.run_id, {})
            old = bucket.get(w.openalex_id)
            if old is None:
                bucket[w.openalex_id] = w
            else:
                labels = tuple(dict.fromkeys((*old.query_labels, *w.query_labels)))
                bucket[w.openalex_id] = replace(old, query_labels=labels)

    async def list(self, run_id: UUID, origin: CorpusOrigin | None = None) -> list[CorpusWork]:
        works = self.data.get(run_id, {}).values()
        return sorted((w for w in works if origin is None or w.origin is origin), key=lambda w: w.openalex_id)

    async def get_many(self, run_id: UUID, openalex_ids: Sequence[str]) -> dict[str, CorpusWork]:
        bucket = self.data.get(run_id, {})
        return {i: bucket[i] for i in openalex_ids if i in bucket}

    async def set_clusters(self, run_id: UUID, assignments: dict[str, UUID]) -> None:
        bucket = self.data.get(run_id, {})
        for oid, cid in assignments.items():
            if oid in bucket:
                bucket[oid] = replace(bucket[oid], cluster_id=cid)


class InMemoryClusterRepo:
    def __init__(self, data: dict[UUID, dict[UUID, Cluster]]) -> None:
        self.data = data

    async def upsert_many(self, clusters: Sequence[Cluster]) -> None:
        for c in clusters:
            self.data.setdefault(c.run_id, {})[c.id] = copy.deepcopy(c)

    async def list(self, run_id: UUID) -> list[Cluster]:
        return sorted(
            (copy.deepcopy(c) for c in self.data.get(run_id, {}).values()),
            key=lambda c: (c.is_misc, c.label_key),
        )


class InMemoryProbeRepo:
    def __init__(self, data: dict[UUID, dict[UUID, NoveltyProbe]]) -> None:
        self.data = data

    async def upsert_many(self, probes: Sequence[NoveltyProbe]) -> None:
        for p in probes:
            self.data.setdefault(p.run_id, {})[p.id] = copy.deepcopy(p)

    async def list(self, run_id: UUID) -> list[NoveltyProbe]:
        return sorted((copy.deepcopy(p) for p in self.data.get(run_id, {}).values()), key=lambda p: p.label)


class InMemoryOpportunityRepo:
    def __init__(self, data: dict[UUID, Opportunity]) -> None:
        self.data = data

    async def add_drafts(self, opportunities: Sequence[Opportunity]) -> None:
        for o in opportunities:
            self.data.setdefault(o.id, copy.deepcopy(o))

    async def get(self, opportunity_id: UUID) -> Opportunity | None:
        return copy.deepcopy(self.data.get(opportunity_id))

    async def list_for_run(self, run_id: UUID) -> list[Opportunity]:
        opps = [o for o in self.data.values() if o.run_id == run_id]
        return [copy.deepcopy(o) for o in sorted(opps, key=lambda o: (o.batch, o.ord))]

    async def save(self, opportunity: Opportunity) -> None:
        self.data[opportunity.id] = copy.deepcopy(opportunity)

    async def previous_titles(self, owner_id: AccountId, exclude_run: UUID) -> list[tuple[UUID, str]]:
        seen = {"shown", "saved", "dismissed"}
        opps = sorted(
            (o for o in self.data.values() if o.owner_id == owner_id and o.run_id != exclude_run and o.status.value in seen),
            key=lambda o: o.created_at, reverse=True,
        )  # fmt: skip
        return [(o.id, o.title) for o in opps]


class InMemoryDiscoveryUoW:
    def __init__(self, store: DiscoveryStore) -> None:
        self._store = store

    async def __aenter__(self) -> Self:
        s = self._store
        self._r = {k: copy.deepcopy(v) for k, v in s.runs.items()}
        self._c = {k: dict(v) for k, v in s.corpus.items()}
        self._k = {k: copy.deepcopy(v) for k, v in s.clusters.items()}
        self._p = {k: copy.deepcopy(v) for k, v in s.probes.items()}
        self._o = {k: copy.deepcopy(v) for k, v in s.opportunities.items()}
        self._runs, self._corpus = InMemoryRunRepo(self._r), InMemoryCorpusRepo(self._c)
        self._clusters, self._probes = InMemoryClusterRepo(self._k), InMemoryProbeRepo(self._p)
        self._opps = InMemoryOpportunityRepo(self._o)
        self._audit = InMemoryAuditLog()
        return self

    @property
    def runs(self) -> InMemoryRunRepo:
        return self._runs

    @property
    def corpus(self) -> InMemoryCorpusRepo:
        return self._corpus

    @property
    def clusters(self) -> InMemoryClusterRepo:
        return self._clusters

    @property
    def probes(self) -> InMemoryProbeRepo:
        return self._probes

    @property
    def opportunities(self) -> InMemoryOpportunityRepo:
        return self._opps

    @property
    def audit(self) -> InMemoryAuditLog:
        return self._audit

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        s = self._store
        for target, staged in (
            (s.runs, self._r), (s.corpus, self._c), (s.clusters, self._k), (s.probes, self._p), (s.opportunities, self._o),
        ):  # fmt: skip
            target.clear()
            target.update(staged)  # type: ignore[arg-type]
        s.audit.entries.extend(self._audit.entries)
        s.commits += 1

    async def rollback(self) -> None:
        return None


class InMemoryLedger:
    """Adds to the committed run totals immediately (its own transaction in the real adapter)."""

    def __init__(self, store: DiscoveryStore, clock: Clock) -> None:
        self._store, self._clock = store, clock
        self.charges: list[dict[str, Any]] = []
        self.on_charge: Callable[[int], None] | None = None  # test hook: called with the charge number

    async def charge(
        self,
        run_id: UUID,
        generation: int,
        *,
        cost_usd: Decimal = Decimal(0),
        llm_calls: int = 0,
        openalex_calls: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> LedgerState:
        run = self._store.runs[run_id]
        if run.generation != generation:
            return LedgerState(run.spend, run.cancel_requested_at is not None, superseded=True)
        self.charges.append({"cost_usd": cost_usd, "llm_calls": llm_calls, "openalex_calls": openalex_calls})
        s = run.spend
        run.spend = RunSpend(
            s.cost_usd + cost_usd, s.llm_calls + llm_calls, s.openalex_calls + openalex_calls,
            s.tokens_in + tokens_in, s.tokens_out + tokens_out,
        )  # fmt: skip
        run.heartbeat_at = self._clock.now()
        if self.on_charge:
            self.on_charge(len(self.charges))
        return LedgerState(run.spend, run.cancel_requested_at is not None, superseded=False)


class FakeProfileSource:
    def __init__(self) -> None:
        self.profiles: dict[AccountId, RunProfile] = {}

    async def run_profile(self, owner_id: AccountId) -> RunProfile | None:
        return self.profiles.get(owner_id)


class FakeVaultSource:
    def __init__(self) -> None:
        self.docs: dict[UUID, tuple[AccountId, DocMaterial, bool]] = {}  # id -> (owner, material, ready)

    def add(self, owner: AccountId, material: DocMaterial, ready: bool = True) -> None:
        self.docs[material.document_id] = (owner, material, ready)

    def delete(self, document_id: UUID) -> None:
        self.docs.pop(document_id, None)

    async def check_documents(
        self, owner_id: AccountId, document_ids: tuple[UUID, ...]
    ) -> list[DocumentCheck]:
        out = []
        for d in document_ids:
            owner, mat, ready = self.docs.get(d, (None, None, False))
            exists = owner == owner_id and mat is not None
            out.append(DocumentCheck(d, exists, exists and ready, mat.retention if mat else "discard"))
        return out

    async def run_material(
        self, owner_id: AccountId, labelled: tuple[tuple[str, UUID], ...]
    ) -> list[DocMaterial]:
        out: list[DocMaterial] = []
        for label, d in labelled:
            owner, mat, _ = self.docs.get(d, (None, None, False))
            if owner == owner_id and mat is not None:
                out.append(replace(mat, label=label))
            else:
                out.append(DocMaterial(d, label, "", "discard", active=False))
        return out

    async def passage(self, owner_id: AccountId, chunk_id: UUID) -> PassageView | None:
        for owner, mat, _ in self.docs.values():
            if owner == owner_id:
                for p in mat.passages:
                    if p.chunk_id == chunk_id:
                        return p
        return None


class AgentKilled(BaseException):
    """Simulates the worker process dying mid-call (not an ``Exception``: nothing may swallow it)."""


class FakeDiscoveryAgents:
    """Scripted agents with a call log, a cost per call and a kill switch."""

    def __init__(self, cost: Decimal = Decimal("0.01")) -> None:
        self.cost = cost
        self.calls: list[tuple[str, Any]] = []
        self.scripts: dict[str, Any] = {}
        self.kill_on_call: int | None = (
            None  # kill (raise AgentKilled) on the n-th call overall, counting from 1
        )

    def script(self, agent: str, output: Any) -> None:
        """``output`` is a model instance, or a function of the input returning one."""
        self.scripts[agent] = output

    def count(self, agent: str) -> int:
        return sum(1 for a, _ in self.calls if a == agent)

    def _outcome(self, agent: str, data: Any, default: Any) -> AgentOutcome[Any]:
        self.calls.append((agent, data))
        if self.kill_on_call is not None and len(self.calls) == self.kill_on_call:
            raise AgentKilled
        out = self.scripts.get(agent, default)
        out = out(data) if callable(out) else out
        return AgentOutcome(
            out, f"{agent}@test", "fake", self.cost, TokenUsage(input_tokens=100, output_tokens=50)
        )

    async def plan_queries(self, data: QueryPlannerInput, ctx: CallContext) -> AgentOutcome[QueryPlan]:
        return self._outcome("QueryPlanner", data, None)

    async def label_cluster(self, data: ClusterLabelInput, ctx: CallContext) -> AgentOutcome[ClusterLabel]:
        return self._outcome(
            "ClusterLabeler", data,
            lambda d: ClusterLabel(cluster_id=d.cluster_id, label="Label " + d.cluster_id, search_terms=["term", "other"]),
        )  # fmt: skip

    async def tag_insights(
        self, data: InsightTopicTaggerInput, ctx: CallContext
    ) -> AgentOutcome[InsightTopicTags]:
        return self._outcome("InsightTopicTagger", data, InsightTopicTags(tags=[]))

    async def analyse(self, data: GapContext, ctx: CallContext) -> AgentOutcome[GapAnalysis]:
        return self._outcome("GapAnalyst", data, GapAnalysis(opportunities=[]))

    async def critique(self, data: CriticInput, ctx: CallContext) -> AgentOutcome[CriticVerdict]:
        return self._outcome("Critic", data, CriticVerdict(claims=[], keep=False))


__all__ = [
    "AgentKilled", "DiscoveryStore", "FakeDiscoveryAgents", "FakeProfileSource", "FakeVaultSource",
    "InMemoryDiscoveryUoW", "InMemoryLedger",
]  # fmt: skip
