"""Shared plumbing of the run pipeline: configuration, the per-execution ``StepContext`` and small helpers."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeVar
from uuid import UUID

from scout.discovery.application.guard import BudgetedLiterature, RunGuard
from scout.discovery.domain.corpus import CorpusWork
from scout.discovery.domain.enums import CorpusOrigin, RunStatus
from scout.discovery.domain.field import FieldConfig
from scout.discovery.domain.ids import corpus_work_id
from scout.discovery.domain.run import PUBLISHED_ONLY, AnalysisRun, Superseded
from scout.discovery.ports.agents import AgentOutcome, DiscoveryAgentsPort
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.discovery.ports.sources import DocMaterial, RunProfile, VaultSource
from scout.ports.literature import WorkRecord
from scout.ports.llm import CallContext
from scout.shared import AccountId, Clock
from scout.shared.text import clean_text

if TYPE_CHECKING:
    from scout.discovery.domain.run import RunInputs

T = TypeVar("T")


@dataclass(frozen=True)
class PipelineConfig:
    """Tunables of one execution (settings carry the same names, DECISIONS 62)."""

    llm_concurrency: int = 3
    openalex_concurrency: int = 4
    analyse_max_calls: int = 2
    candidates_per_batch: int = 4
    max_resolve: int = 20
    max_profile_works: int = 300
    max_queries: int = 25
    max_shown: int = 10


@dataclass
class StepContext:
    """Everything a step needs. One per execution; ``generation`` fences every write."""

    run_id: UUID
    owner_id: AccountId
    session_id: UUID | None
    generation: int
    uow: Callable[[], DiscoveryUnitOfWork]
    guard: RunGuard
    literature: BudgetedLiterature
    agents: DiscoveryAgentsPort
    vault: VaultSource
    profile: RunProfile
    field: FieldConfig
    cfg: PipelineConfig
    clock: Clock
    last_attempt: bool = False
    _llm_sem: asyncio.Semaphore = dataclasses.field(init=False)
    _oa_sem: asyncio.Semaphore = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self._llm_sem = asyncio.Semaphore(self.cfg.llm_concurrency)
        self._oa_sem = asyncio.Semaphore(self.cfg.openalex_concurrency)

    def call_ctx(self, feature: str) -> CallContext:
        """Attribution of a model call."""
        return CallContext(
            feature=feature, account_id=self.owner_id, session_id=self.session_id, run_id=self.run_id
        )

    async def read(self) -> AnalysisRun:
        """A fresh copy of the run (no lock)."""
        async with self.uow() as uow:
            run = await uow.runs.get(self.run_id, self.owner_id)
        if run is None or run.generation != self.generation:
            raise Superseded("generation_changed")
        return run

    async def tx(self, work: Callable[[DiscoveryUnitOfWork, AnalysisRun], Awaitable[T]]) -> T:
        """One fenced transaction: lock the run, check the generation, run ``work``, save the run, commit."""
        async with self.uow() as uow:
            run = await uow.runs.get_for_update(self.run_id)
            if run is None or run.generation != self.generation or run.status is not RunStatus.RUNNING:
                raise Superseded("generation_changed")
            result = await work(uow, run)
            await uow.runs.save(run)
            await uow.commit()
            return result

    async def ask(
        self, tier: Literal["fast", "reasoning"], call: Callable[[], Awaitable[AgentOutcome[T]]]
    ) -> AgentOutcome[T]:
        """One budgeted, reserved model call (at most ``llm_concurrency`` run at once)."""
        async with self._llm_sem, self.guard.llm(tier) as ticket:
            outcome = await call()
            ticket.settle(outcome.cost_usd, outcome.usage)
        return outcome

    async def material(self, run: AnalysisRun) -> list[DocMaterial]:
        """Fresh vault material of the run's documents (a deleted document comes back inactive)."""
        if not self.agents.confidential_allowed():  # ADR 016: no authorized endpoint, so published data only
            return []
        labelled = tuple((d.label, d.document_id) for d in run.inputs.documents)
        return await self.vault.run_material(self.owner_id, labelled) if labelled else []

    @staticmethod
    def tainted(run: AnalysisRun) -> bool:
        """Whether text this run produced or selected may derive from vault material (ADR 016, provenance).

        True once the run was started on a full basis with documents, whatever the authorization is now: drafts
        written while the vault was in play stay confidential after authorization is withdrawn."""
        return run.inputs.basis != PUBLISHED_ONLY and bool(run.inputs.documents)

    def basis(self) -> str:
        """``published_only`` when confidential (vault) material is left out of this run, else ``full``."""
        return "full" if self.agents.confidential_allowed() else PUBLISHED_ONLY


def to_corpus(run_id: UUID, rec: WorkRecord, origin: CorpusOrigin, label: str | None = None) -> CorpusWork:
    """A cleaned corpus work from an OpenAlex record (display-only authors, bounded strings)."""
    topics = tuple({"id": t.id, "name": t.name, "subfield": t.subfield} for t in rec.topics)
    return CorpusWork(
        id=corpus_work_id(run_id, rec.id),
        run_id=run_id,
        openalex_id=rec.id,
        title=clean_text(rec.title, 500),
        abstract=clean_text(rec.abstract, 4000) if rec.abstract else None,
        year=rec.year,
        cited_by=rec.cited_by_count,
        type=rec.type,
        doi=rec.doi.lower() if rec.doi else None,
        topics=topics,
        primary_topic_id=topics[0]["id"] if topics else None,
        authors=tuple(clean_text(n, 80) for n in rec.author_names[:5]),
        origin=origin,
        query_labels=(label,) if label else (),
    )


def insight_refs(material: list[DocMaterial]) -> tuple[str, ...]:
    """Insight references of the active documents, in document order."""
    return tuple(i.ref for m in material if m.active for i in m.insights)


def with_inputs(run: AnalysisRun, **changes: object) -> RunInputs:
    """A copy of ``run.inputs`` with fields replaced."""
    from dataclasses import replace

    return replace(run.inputs, **changes)  # type: ignore[arg-type]
