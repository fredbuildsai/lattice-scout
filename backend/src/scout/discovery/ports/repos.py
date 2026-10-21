"""Discovery persistence ports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from scout.discovery.domain.corpus import Cluster, CorpusWork, NoveltyProbe
from scout.discovery.domain.enums import CorpusOrigin
from scout.discovery.domain.opportunity import Opportunity
from scout.discovery.domain.run import AnalysisRun
from scout.ports.audit import AuditLog
from scout.shared import AccountId, Conflict, UnitOfWork


class RunInProgress(Conflict):
    """The owner already has an active run (the unique index on active runs was hit)."""

    code = "run_in_progress"
    title = "A run is already in progress"


@dataclass(frozen=True)
class StaleRuns:
    """Runs the sweeper must act on."""

    queued: tuple[UUID, ...] = ()
    stalled: tuple[UUID, ...] = ()
    exhausted: tuple[UUID, ...] = ()  # stalled and out of executions: to fail


class RunRepo(Protocol):
    """Analysis runs (with their attached documents)."""

    async def get(self, run_id: UUID, owner_id: AccountId) -> AnalysisRun | None:
        """Owner's run, or None."""
        ...

    async def get_for_update(self, run_id: UUID) -> AnalysisRun | None:
        """Worker side: any owner, row locked until the transaction ends."""
        ...

    async def add(self, run: AnalysisRun) -> None:
        """Insert. Raises ``RunInProgress`` when the owner has another active run."""
        ...

    async def save(self, run: AnalysisRun) -> None:
        """Persist the aggregate."""
        ...

    async def active_for(self, owner_id: AccountId) -> AnalysisRun | None:
        """The owner's queued or running run."""
        ...

    async def count_started_since(self, owner_id: AccountId, since: datetime) -> int:
        """Runs created since ``since``."""
        ...

    async def spend_since(self, owner_id: AccountId, since: datetime) -> Decimal:
        """Money spent by the owner's runs created since ``since``."""
        ...

    async def list_for(self, owner_id: AccountId, limit: int) -> list[AnalysisRun]:
        """Newest first."""
        ...

    async def list_stale(
        self, queued_before: datetime, heartbeat_before: datetime, max_executions: int, limit: int
    ) -> StaleRuns:
        """Queued runs older than ``queued_before`` and running runs whose heartbeat is older than ``heartbeat_before``."""
        ...


class CorpusRepo(Protocol):
    """Works of a run's corpus."""

    async def upsert_many(self, works: Sequence[CorpusWork]) -> None:
        """Insert new works; for an existing ``(run, openalex id)`` only append ``query_labels``."""
        ...

    async def list(self, run_id: UUID, origin: CorpusOrigin | None = None) -> list[CorpusWork]:
        """All works of the run (optionally one origin), ordered by OpenAlex id."""
        ...

    async def get_many(self, run_id: UUID, openalex_ids: Sequence[str]) -> dict[str, CorpusWork]:
        """Works by OpenAlex id."""
        ...

    async def set_clusters(self, run_id: UUID, assignments: dict[str, UUID]) -> None:
        """Set ``cluster_id`` for the given OpenAlex ids."""
        ...


class ClusterRepo(Protocol):
    """Topic clusters of a run."""

    async def upsert_many(self, clusters: Sequence[Cluster]) -> None:
        """Insert or update by id (deterministic ids make re-runs idempotent)."""
        ...

    async def list(self, run_id: UUID) -> list[Cluster]:
        """All clusters ordered by label key."""
        ...


class ProbeRepo(Protocol):
    """Novelty probes of a run."""

    async def upsert_many(self, probes: Sequence[NoveltyProbe]) -> None:
        """Insert or update by id."""
        ...

    async def list(self, run_id: UUID) -> list[NoveltyProbe]:
        """All probes ordered by label."""
        ...


class OpportunityRepo(Protocol):
    """Opportunities with their claims and evidence."""

    async def add_drafts(self, opportunities: Sequence[Opportunity]) -> None:
        """Insert new aggregates; ones whose id exists are left alone (a re-run of ``analyse``)."""
        ...

    async def get(self, opportunity_id: UUID) -> Opportunity | None:
        """The aggregate (any status; read models filter)."""
        ...

    async def list_for_run(self, run_id: UUID) -> list[Opportunity]:
        """All opportunities of the run in ``(batch, ord)`` order."""
        ...

    async def save(self, opportunity: Opportunity) -> None:
        """Persist status, scores and every claim and evidence row of the aggregate."""
        ...

    async def previous_titles(self, owner_id: AccountId, exclude_run: UUID) -> list[tuple[UUID, str]]:
        """Titles of the owner's earlier ``shown``, ``saved`` and ``dismissed`` opportunities (newest first)."""
        ...


class DiscoveryUnitOfWork(UnitOfWork, Protocol):
    """Transaction for discovery use cases."""

    @property
    def runs(self) -> RunRepo:
        """Runs."""
        ...

    @property
    def corpus(self) -> CorpusRepo:
        """Corpus."""
        ...

    @property
    def clusters(self) -> ClusterRepo:
        """Clusters."""
        ...

    @property
    def probes(self) -> ProbeRepo:
        """Probes."""
        ...

    @property
    def opportunities(self) -> OpportunityRepo:
        """Opportunities."""
        ...

    @property
    def audit(self) -> AuditLog:
        """Audit log."""
        ...
