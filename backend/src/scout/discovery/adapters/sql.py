"""SQL repositories, run ledger and unit of work for the discovery context."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.audit import SqlAuditLog
from scout.adapters.persistence.db import Database
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.discovery.adapters import serde
from scout.discovery.domain.budget import RunSpend
from scout.discovery.domain.corpus import Cluster, CorpusWork, NoveltyProbe
from scout.discovery.domain.enums import CorpusOrigin, OppKind, OppStatus
from scout.discovery.domain.opportunity import Opportunity
from scout.discovery.domain.run import AnalysisRun, RunDocument
from scout.discovery.ports.ledger import LedgerState
from scout.discovery.ports.repos import RunInProgress, StaleRuns
from scout.ports.audit import AuditLog
from scout.shared import AccountId, Clock


class SqlRunRepo:
    """Implements ``RunRepo``. ``save`` never touches the ledger-owned columns (spend, heartbeat)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _load(self, rows: Sequence[Any]) -> list[AnalysisRun]:
        runs = [serde.run_from_row(r) for r in rows]
        if runs:
            docs = await self._s.execute(
                select(m.run_document)
                .where(m.run_document.c.run_id.in_([r.id for r in runs]))
                .order_by(m.run_document.c.label)
            )
            by_run: dict[UUID, list[Any]] = {}
            for d in docs:
                by_run.setdefault(d.run_id, []).append(d)
            for r in runs:
                retention = {x.document_id: x.retention for x in r.inputs.documents}
                r.inputs = type(r.inputs)(
                    r.inputs.researcher_id, r.inputs.topics,
                    tuple(RunDocument(d.label, d.document_id, retention.get(d.document_id, "discard")) for d in by_run.get(r.id, [])),
                    r.inputs.insight_refs, r.inputs.year_from, r.inputs.novelty_from,
                )  # fmt: skip
        return runs

    async def get(self, run_id: UUID, owner_id: AccountId) -> AnalysisRun | None:
        """Owner's run."""
        t = m.analysis_run
        rows = (await self._s.execute(select(t).where(t.c.id == run_id, t.c.owner_id == owner_id))).all()
        return (await self._load(rows))[0] if rows else None

    async def get_for_update(self, run_id: UUID) -> AnalysisRun | None:
        """Any owner, row locked."""
        t = m.analysis_run
        rows = (await self._s.execute(select(t).where(t.c.id == run_id).with_for_update())).all()
        return (await self._load(rows))[0] if rows else None

    async def add(self, run: AnalysisRun) -> None:
        """Insert; a second active run of the owner raises ``RunInProgress``."""
        try:
            async with self._s.begin_nested():
                await self._s.execute(
                    insert(m.analysis_run).values(
                        id=run.id,
                        created_at=run.created_at,
                        cost_usd=run.spend.cost_usd,
                        llm_calls=0,
                        openalex_calls=0,
                        tokens_in=0,
                        tokens_out=0,
                        heartbeat_at=run.heartbeat_at,
                        **serde.run_values(run),
                    )  # fmt: skip
                )
        except IntegrityError as exc:
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                != "analysis_run_one_active_idx"
            ):
                raise
            raise RunInProgress("An analysis run is already in progress") from exc
        for d in run.inputs.documents:
            await self._s.execute(
                insert(m.run_document).values(run_id=run.id, document_id=d.document_id, label=d.label)
            )

    async def save(self, run: AnalysisRun) -> None:
        """Update everything except the spend counters and ``heartbeat_at`` (owned by the ledger)."""
        await self._s.execute(
            update(m.analysis_run).where(m.analysis_run.c.id == run.id).values(**serde.run_values(run))
        )

    async def active_for(self, owner_id: AccountId) -> AnalysisRun | None:
        """Queued or running run."""
        t = m.analysis_run
        rows = (
            await self._s.execute(
                select(t).where(t.c.owner_id == owner_id, t.c.status.in_(("queued", "running")))
            )
        ).all()
        return (await self._load(rows))[0] if rows else None

    async def count_started_since(self, owner_id: AccountId, since: datetime) -> int:
        """Runs created since ``since``."""
        t = m.analysis_run
        n = await self._s.scalar(
            select(func.count()).select_from(t).where(t.c.owner_id == owner_id, t.c.created_at >= since)
        )
        return int(n or 0)

    async def spend_since(self, owner_id: AccountId, since: datetime) -> Decimal:
        """Money spent by runs created since ``since``."""
        t = m.analysis_run
        v = await self._s.scalar(
            select(func.coalesce(func.sum(t.c.cost_usd), 0)).where(
                t.c.owner_id == owner_id, t.c.created_at >= since
            )
        )
        return Decimal(v or 0)

    async def list_for(self, owner_id: AccountId, limit: int) -> list[AnalysisRun]:
        """Newest first."""
        t = m.analysis_run
        rows = (
            await self._s.execute(
                select(t).where(t.c.owner_id == owner_id).order_by(t.c.created_at.desc()).limit(limit)
            )
        ).all()
        return await self._load(rows)

    async def list_stale(
        self, queued_before: datetime, heartbeat_before: datetime, max_executions: int, limit: int
    ) -> StaleRuns:
        """Queued and stalled runs for the sweeper."""
        t = m.analysis_run

        async def ids(*where: Any) -> tuple[UUID, ...]:
            rows = await self._s.execute(select(t.c.id).where(*where).order_by(t.c.created_at).limit(limit))
            return tuple(r[0] for r in rows)

        stalled = (
            t.c.status == "running",
            func.coalesce(t.c.heartbeat_at, t.c.created_at) < heartbeat_before,
        )
        return StaleRuns(
            queued=await ids(t.c.status == "queued", t.c.created_at < queued_before),
            stalled=await ids(*stalled, t.c.executions < max_executions),
            exhausted=await ids(*stalled, t.c.executions >= max_executions),
        )


class SqlCorpusRepo:
    """Implements ``CorpusRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _work(r: Any) -> CorpusWork:
        return CorpusWork(
            id=r.id, run_id=r.run_id, openalex_id=r.openalex_work_id, title=r.title, abstract=r.abstract, year=r.year,
            cited_by=r.cited_by, type=r.type, doi=r.doi, topics=tuple(r.topics), primary_topic_id=r.primary_topic_id,
            authors=tuple(r.authors), origin=CorpusOrigin(r.origin), query_labels=tuple(r.query_labels), cluster_id=r.cluster_id,
        )  # fmt: skip

    async def upsert_many(self, works: Sequence[CorpusWork]) -> None:
        """Insert; an existing ``(run, openalex id)`` only gains new query labels."""
        for w in works:
            stmt = pg_insert(m.corpus_work).values(
                id=w.id, run_id=w.run_id, openalex_work_id=w.openalex_id, doi=w.doi, title=w.title, abstract=w.abstract,
                year=w.year, cited_by=w.cited_by, type=w.type, topics=list(w.topics), primary_topic_id=w.primary_topic_id,
                authors=list(w.authors), origin=w.origin.value, query_labels=list(w.query_labels), cluster_id=w.cluster_id,
            )  # fmt: skip
            merged = text(
                "(SELECT coalesce(array_agg(DISTINCT x), '{}') FROM unnest(corpus_work.query_labels || EXCLUDED.query_labels) AS x)"
            )
            await self._s.execute(
                stmt.on_conflict_do_update(
                    index_elements=["run_id", "openalex_work_id"], set_={"query_labels": merged}
                )
            )

    async def list(self, run_id: UUID, origin: CorpusOrigin | None = None) -> list[CorpusWork]:
        """Works of the run ordered by OpenAlex id."""
        t = m.corpus_work
        q = select(t).where(t.c.run_id == run_id).order_by(t.c.openalex_work_id)
        if origin is not None:
            q = q.where(t.c.origin == origin.value)
        return [self._work(r) for r in await self._s.execute(q)]

    async def get_many(self, run_id: UUID, openalex_ids: Sequence[str]) -> dict[str, CorpusWork]:
        """Works by OpenAlex id."""
        if not openalex_ids:
            return {}
        t = m.corpus_work
        rows = await self._s.execute(
            select(t).where(t.c.run_id == run_id, t.c.openalex_work_id.in_(list(openalex_ids)))
        )
        return {r.openalex_work_id: self._work(r) for r in rows}

    async def set_clusters(self, run_id: UUID, assignments: dict[str, UUID]) -> None:
        """Assign works to clusters."""
        t = m.corpus_work
        for oid, cid in assignments.items():
            await self._s.execute(
                update(t).where(t.c.run_id == run_id, t.c.openalex_work_id == oid).values(cluster_id=cid)
            )


class SqlClusterRepo:
    """Implements ``ClusterRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, clusters: Sequence[Cluster]) -> None:
        """Insert or update by id."""
        for c in clusters:
            vals = {
                "run_id": c.run_id, "key": c.key, "label_key": c.label_key, "label": c.label, "description": c.description,
                "search_terms": c.search_terms, "label_source": c.label_source, "topic_ids": c.topic_ids,
                "subfield": c.subfield, "size": c.size, "user_works": c.user_works, "insight_weight": c.insight_weight,
                "user_coverage": c.user_coverage, "density": c.density, "candidate_rank": c.candidate_rank,
                "is_misc": c.is_misc,
            }  # fmt: skip
            stmt = pg_insert(m.cluster).values(id=c.id, **vals)
            await self._s.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=vals))

    async def list(self, run_id: UUID) -> list[Cluster]:
        """Clusters ordered with ``misc`` last."""
        t = m.cluster
        rows = await self._s.execute(
            select(t).where(t.c.run_id == run_id).order_by(t.c.is_misc, t.c.label_key)
        )
        return [
            Cluster(
                id=r.id,
                run_id=r.run_id,
                key=r.key,
                label_key=r.label_key,
                label=r.label,
                description=r.description,
                search_terms=list(r.search_terms),
                label_source=r.label_source,
                topic_ids=list(r.topic_ids),
                subfield=r.subfield,
                size=r.size,
                is_misc=r.is_misc,
                user_works=r.user_works,
                insight_weight=r.insight_weight,
                user_coverage=r.user_coverage,
                density=r.density,
                candidate_rank=r.candidate_rank,
            )  # fmt: skip
            for r in rows
        ]


class SqlProbeRepo:
    """Implements ``ProbeRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def upsert_many(self, probes: Sequence[NoveltyProbe]) -> None:
        """Insert or update by id."""
        for p in probes:
            vals = {
                "run_id": p.run_id, "label": p.label, "cluster_id": p.cluster_id, "query": p.query,
                "from_date": p.from_date, "count": p.count, "status": p.status, "measured_at": p.measured_at,
            }  # fmt: skip
            await self._s.execute(
                pg_insert(m.novelty_probe)
                .values(id=p.id, **vals)
                .on_conflict_do_update(index_elements=["id"], set_=vals)
            )

    async def list(self, run_id: UUID) -> list[NoveltyProbe]:
        """Probes ordered by label."""
        t = m.novelty_probe
        rows = await self._s.execute(select(t).where(t.c.run_id == run_id).order_by(t.c.label))
        return [
            NoveltyProbe(
                r.id,
                r.run_id,
                r.label,
                r.cluster_id,
                r.query,
                r.from_date,
                r._mapping["count"],
                r.status,
                r.measured_at,
            )
            for r in rows
        ]


class SqlOpportunityRepo:
    """Implements ``OpportunityRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _write(self, o: Opportunity, *, update_existing: bool) -> None:
        vals = serde.opportunity_values(o)
        stmt = pg_insert(m.opportunity).values(id=o.id, **vals)
        if update_existing:
            vals = {k: v for k, v in vals.items() if k not in ("created_at", "updated_at")}
            await self._s.execute(
                stmt.on_conflict_do_update(index_elements=["id"], set_={**vals, "updated_at": func.now()})
            )
        else:
            await self._s.execute(stmt.on_conflict_do_nothing(index_elements=["id"]))
        for c in o.claims:
            cv = serde.claim_values(c, o.id, o.created_at)
            cstmt = pg_insert(m.claim).values(id=c.id, **cv)
            if update_existing:
                cset = {k: v for k, v in cv.items() if k not in ("created_at", "updated_at")}
                await self._s.execute(
                    cstmt.on_conflict_do_update(
                        index_elements=["id"], set_={**cset, "updated_at": func.now()}
                    )
                )
            else:
                await self._s.execute(cstmt.on_conflict_do_nothing(index_elements=["id"]))
            for e in c.evidence:
                ev = serde.evidence_values(e, c.id)
                estmt = pg_insert(m.evidence).values(id=e.id, **ev)
                if update_existing:
                    await self._s.execute(estmt.on_conflict_do_update(index_elements=["id"], set_=ev))
                else:
                    await self._s.execute(estmt.on_conflict_do_nothing(index_elements=["id"]))

    async def add_drafts(self, opportunities: Sequence[Opportunity]) -> None:
        """Insert new aggregates; existing ids are left alone."""
        for o in opportunities:
            await self._write(o, update_existing=False)

    async def save(self, opportunity: Opportunity) -> None:
        """Persist the aggregate, claims and evidence included."""
        await self._write(opportunity, update_existing=True)

    async def _aggregates(self, rows: list[Any]) -> list[Opportunity]:
        if not rows:
            return []
        ids = [r.id for r in rows]
        claims = list(await self._s.execute(select(m.claim).where(m.claim.c.opportunity_id.in_(ids))))
        evidence = list(
            await self._s.execute(select(m.evidence).where(m.evidence.c.claim_id.in_([c.id for c in claims])))
        )
        return [
            serde.opportunity_from_rows(
                r, [c for c in claims if c.opportunity_id == r.id],
                [e for e in evidence if e.claim_id in {c.id for c in claims if c.opportunity_id == r.id}],
            )
            for r in rows
        ]  # fmt: skip

    async def get(self, opportunity_id: UUID) -> Opportunity | None:
        """The aggregate."""
        rows = list(await self._s.execute(select(m.opportunity).where(m.opportunity.c.id == opportunity_id)))
        out = await self._aggregates(rows)
        return out[0] if out else None

    async def list_for_run(self, run_id: UUID) -> list[Opportunity]:
        """All opportunities of a run in ``(batch, ord)`` order."""
        t = m.opportunity
        rows = list(await self._s.execute(select(t).where(t.c.run_id == run_id).order_by(t.c.batch, t.c.ord)))
        return await self._aggregates(rows)

    async def previous_titles(self, owner_id: AccountId, exclude_run: UUID) -> list[tuple[UUID, str]]:
        """Titles of earlier user-visible opportunities (newest first)."""
        t = m.opportunity
        rows = await self._s.execute(
            select(t.c.id, t.c.title)
            .where(
                t.c.owner_id == owner_id,
                t.c.run_id != exclude_run,
                t.c.status.in_(("shown", "saved", "dismissed")),
            )
            .order_by(t.c.created_at.desc())
        )
        return [(r.id, r.title) for r in rows]

    async def list_for_owner(
        self,
        owner_id: AccountId,
        statuses: Sequence[OppStatus],
        run_id: UUID | None = None,
        kind: OppKind | None = None,
    ) -> list[Opportunity]:
        """The owner's opportunities in ``statuses``, best rank first."""
        t = m.opportunity
        q = select(t).where(t.c.owner_id == owner_id, t.c.status.in_([x.value for x in statuses]))
        if run_id is not None:
            q = q.where(t.c.run_id == run_id)
        if kind is not None:
            q = q.where(t.c.kind == kind.value)
        rows = list(
            await self._s.execute(
                q.order_by(t.c.rank_score.desc().nulls_last(), t.c.created_at.desc(), t.c.id)
            )
        )
        return await self._aggregates(rows)

    async def find_by_evidence(self, evidence_id: UUID) -> Opportunity | None:
        """The aggregate holding an evidence item."""
        row = (
            await self._s.execute(
                select(m.claim.c.opportunity_id)
                .select_from(m.evidence.join(m.claim, m.evidence.c.claim_id == m.claim.c.id))
                .where(m.evidence.c.id == evidence_id)
            )
        ).first()
        return await self.get(row.opportunity_id) if row else None

    async def list_recent(self, limit: int, offset: int = 0, run_id: UUID | None = None) -> list[Opportunity]:
        """Newest first, any owner and status."""
        t = m.opportunity
        q = select(t)
        if run_id is not None:
            q = q.where(t.c.run_id == run_id)
        rows = list(
            await self._s.execute(q.order_by(t.c.created_at.desc(), t.c.id).limit(limit).offset(offset))
        )
        return await self._aggregates(rows)


class SqlRunLedger:
    """Implements ``RunLedgerPort``: one short transaction per charge, fenced by the generation."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db, self._clock = db, clock

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
        """Add to the totals if ``generation`` is still current."""
        now = self._clock.now()
        async with self._db.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        UPDATE analysis_run SET cost_usd = cost_usd + :c, llm_calls = llm_calls + :l,
                          openalex_calls = openalex_calls + :o, tokens_in = tokens_in + :ti, tokens_out = tokens_out + :to,
                          heartbeat_at = :now
                        WHERE id = :id AND generation = :g
                        RETURNING cost_usd, llm_calls, openalex_calls, tokens_in, tokens_out, cancel_requested_at
                        """
                    ),
                    {
                        "c": cost_usd,
                        "l": llm_calls,
                        "o": openalex_calls,
                        "ti": tokens_in,
                        "to": tokens_out,
                        "now": now,
                        "id": run_id,
                        "g": generation,
                    },
                )
            ).first()
            if row is not None:
                return LedgerState(
                    RunSpend(row[0], row[1], row[2], row[3], row[4]), row[5] is not None, superseded=False
                )
            cur = (
                await conn.execute(
                    text(
                        "SELECT cost_usd, llm_calls, openalex_calls, tokens_in, tokens_out, cancel_requested_at FROM analysis_run WHERE id = :id"
                    ),
                    {"id": run_id},
                )
            ).first()
        spend = RunSpend(cur[0], cur[1], cur[2], cur[3], cur[4]) if cur else RunSpend()
        return LedgerState(spend, bool(cur and cur[5]), superseded=True)


class SqlDiscoveryUnitOfWork(SqlUnitOfWork):
    """Discovery transaction."""

    runs: SqlRunRepo
    corpus: SqlCorpusRepo
    clusters: SqlClusterRepo
    probes: SqlProbeRepo
    opportunities: SqlOpportunityRepo
    audit: AuditLog

    def _bind(self) -> None:
        self.runs = SqlRunRepo(self.session)
        self.corpus = SqlCorpusRepo(self.session)
        self.clusters = SqlClusterRepo(self.session)
        self.probes = SqlProbeRepo(self.session)
        self.opportunities = SqlOpportunityRepo(self.session)
        self.audit = SqlAuditLog(self.session)


__all__ = ["SqlDiscoveryUnitOfWork", "SqlRunLedger"]
