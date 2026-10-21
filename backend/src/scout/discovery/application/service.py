"""``DiscoveryService``: the read side and the owner's actions (runs, opportunity cards, evidence, admin review).

Everything a user receives passes through ``Opportunity.visible_claims`` and ``show_decision``: an opportunity
that no longer satisfies the show rule (for example after a document was deleted) simply stops being returned.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from uuid import UUID

from scout.discovery.application.start import StartAnalysisRun
from scout.discovery.domain.enums import (
    ClaimSection,
    ClaimStatus,
    OppKind,
    OppStatus,
    SourceType,
    StepStatus,
)
from scout.discovery.domain.opportunity import Claim, Evidence, Opportunity
from scout.discovery.domain.run import STEP_ORDER, AnalysisRun
from scout.discovery.ports.repos import DiscoveryUnitOfWork
from scout.discovery.ports.sources import VaultSource
from scout.ports.audit import AuditEntry
from scout.shared import AccountId, Clock, IdGenerator, NotFound, ValidationFailed

USER_VISIBLE = (OppStatus.SHOWN, OppStatus.SAVED, OppStatus.DISMISSED)
MAX_PAGE = 50


@dataclass(frozen=True)
class StepView:
    """One step of the progress list."""

    name: str
    status: str
    progress: float


@dataclass(frozen=True)
class RunView:
    """A run as the owner sees it (codes and counts only)."""

    run: AnalysisRun
    steps: list[StepView]
    counts: dict[str, int]
    document_ids: list[UUID]


@dataclass(frozen=True)
class MarkerView:
    """One verified citation, resolved for display."""

    id: UUID
    source_type: str
    work_id: str | None = None
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    authors: list[str] = field(default_factory=list)
    quote: str | None = None
    document_id: UUID | None = None
    filename: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    summary: str | None = None
    original_available: bool = False
    query: str | None = None
    since_year: int | None = None
    count: int | None = None


@dataclass(frozen=True)
class ClaimView:
    """A verified claim with its verified citations."""

    id: UUID
    text: str
    markers: list[MarkerView]


@dataclass(frozen=True)
class CardView:
    """List item."""

    opportunity: Opportunity
    why: ClaimView | None
    verified_sources: int
    clusters: list[str]


@dataclass(frozen=True)
class DetailView:
    """Detail page."""

    card: CardView
    rationale: list[ClaimView]
    objection: list[ClaimView]
    score: list[ClaimView]


@dataclass(frozen=True)
class Page[T]:
    """A page of results with an opaque cursor."""

    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True)
class AdminClaimView:
    """A claim for the quality review (vault quotes only when the flag allows)."""

    opportunity: Opportunity
    claim: Claim


@dataclass(frozen=True)
class EvidenceOpen:
    """The source behind a citation, resolved live."""

    kind: str
    id: UUID
    title: str | None = None
    year: int | None = None
    doi: str | None = None
    work_id: str | None = None
    abstract: str | None = None
    quote: str | None = None
    document_id: UUID | None = None
    filename: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    passage: str | None = None
    original_available: bool = False
    query: str | None = None
    count: int | None = None
    since_year: int | None = None


def _encode(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        n = int(base64.urlsafe_b64decode(cursor.encode()).decode())
    except ValueError:
        raise ValidationFailed("Invalid cursor") from None
    if n < 0:
        raise ValidationFailed("Invalid cursor")
    return n


class DiscoveryService:
    """Owner-scoped queries and actions. Another owner's id is always ``NotFound``."""

    def __init__(
        self,
        uow: Callable[[], DiscoveryUnitOfWork],
        start: StartAnalysisRun,
        vault: VaultSource,
        clock: Clock,
        ids: IdGenerator,
        *,
        admin_show_vault_quotes: bool = False,
    ) -> None:
        self._uow, self._start, self._vault, self._clock, self._ids = uow, start, vault, clock, ids
        self._admin_quotes = admin_show_vault_quotes

    # ---- runs ---------------------------------------------------------------------------------------------------
    async def start(
        self, owner_id: AccountId, document_ids: Sequence[UUID], session_id: UUID | None = None
    ) -> RunView:
        """Start a run (see ``StartAnalysisRun.start`` for the errors)."""
        run = await self._start.start(owner_id, document_ids, session_id)
        return await self._run_view(run)

    async def get_run(self, owner_id: AccountId, run_id: UUID) -> RunView:
        """The owner's run."""
        async with self._uow() as uow:
            run = await uow.runs.get(run_id, owner_id)
        if run is None:
            raise NotFound("Run not found")
        return await self._run_view(run)

    async def list_runs(self, owner_id: AccountId, limit: int = 20) -> list[RunView]:
        """Latest runs, newest first."""
        async with self._uow() as uow:
            runs = await uow.runs.list_for(owner_id, limit)
        return [await self._run_view(r) for r in runs]

    async def cancel(self, owner_id: AccountId, run_id: UUID) -> RunView:
        """Ask a run to stop.

        Raises:
            NotFound: not the owner's run.
            InvalidStateTransition: the run already ended.
        """
        async with self._uow() as uow:
            run = await uow.runs.get(run_id, owner_id)
            if run is None:
                raise NotFound("Run not found")
            locked = await uow.runs.get_for_update(run_id)
            assert locked is not None
            locked.request_cancel(self._clock.now())
            await uow.runs.save(locked)
            await uow.audit.add(
                AuditEntry(
                    id=self._ids.new(),
                    actor_id=owner_id,
                    action="run.cancel_requested",
                    target=str(run_id),
                    at=self._clock.now(),
                )
            )
            await uow.commit()
        return await self._run_view(locked)

    async def _run_view(self, run: AnalysisRun) -> RunView:
        async with self._uow() as uow:
            corpus = await uow.corpus.list(run.id)
            clusters = await uow.clusters.list(run.id)
            opps = await uow.opportunities.list_for_run(run.id)
        steps: list[StepView] = []
        for s in STEP_ORDER:
            rec = run.step_state.get(s)
            status = rec.status.value if rec else StepStatus.PENDING.value
            frac = (
                1.0
                if rec and rec.status in (StepStatus.DONE, StepStatus.DEGRADED)
                else (rec.done / rec.total if rec and rec.total else 0.0)
            )
            steps.append(StepView(s.value, status, round(min(1.0, frac), 3)))
        counts = {
            "corpus": len(corpus), "clusters": len(clusters), "candidates": sum(1 for c in clusters if c.candidate_rank is not None),
            "drafts": len(opps), "shown": sum(1 for o in opps if o.status is OppStatus.SHOWN),
        }  # fmt: skip
        return RunView(run, steps, counts, [d.document_id for d in run.inputs.documents])

    # ---- opportunities --------------------------------------------------------------------------------------------
    async def list_opportunities(
        self,
        owner_id: AccountId,
        *,
        run_id: UUID | None = None,
        status: OppStatus | None = None,
        kind: OppKind | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> Page[CardView]:
        """Visible opportunities (``shown`` by default); a foreign run id is ``NotFound``."""
        if status is not None and status not in USER_VISIBLE:
            raise ValidationFailed("status must be shown, saved or dismissed")
        limit = max(1, min(limit, MAX_PAGE))
        offset = _decode(cursor)
        async with self._uow() as uow:
            if run_id is not None and await uow.runs.get(run_id, owner_id) is None:
                raise NotFound("Run not found")
            opps = await uow.opportunities.list_for_owner(
                owner_id, [status] if status else [OppStatus.SHOWN, OppStatus.SAVED], run_id, kind
            )
            labels = await self._cluster_labels(uow, {o.run_id for o in opps})
        visible = [o for o in opps if o.show_decision()[0]]
        page = visible[offset : offset + limit]
        nxt = _encode(offset + limit) if offset + limit < len(visible) else None
        return Page([await self._card(o, labels) for o in page], nxt)

    async def get_opportunity(self, owner_id: AccountId, opportunity_id: UUID) -> DetailView:
        """One visible opportunity with its verified claims."""
        o, labels = await self._owned_visible(owner_id, opportunity_id)
        card = await self._card(o, labels)
        views: dict[ClaimSection, list[ClaimView]] = {s: [] for s in ClaimSection}
        for c in o.visible_claims():
            views[c.section].append(await self._claim_view(o, c))
        return DetailView(
            card, views[ClaimSection.RATIONALE], views[ClaimSection.OBJECTION], views[ClaimSection.SCORE]
        )

    async def set_status(self, owner_id: AccountId, opportunity_id: UUID, new: OppStatus) -> CardView:
        """The owner saves, dismisses or restores an opportunity."""
        await self._owned_visible(owner_id, opportunity_id)
        async with self._uow() as uow:
            o = await uow.opportunities.get(opportunity_id)
            assert o is not None
            o.set_user_status(new)
            await uow.opportunities.save(o)
            await uow.audit.add(
                AuditEntry(id=self._ids.new(), actor_id=owner_id, action="opportunity.status_set", target=str(o.id), at=self._clock.now(), meta={"status": new.value})
            )  # fmt: skip
            await uow.commit()
            labels = await self._cluster_labels(uow, {o.run_id})
        return await self._card(o, labels)

    async def _owned_visible(
        self, owner_id: AccountId, opportunity_id: UUID
    ) -> tuple[Opportunity, dict[UUID, str]]:
        async with self._uow() as uow:
            o = await uow.opportunities.get(opportunity_id)
            if (
                o is None
                or o.owner_id != owner_id
                or o.status not in USER_VISIBLE
                or not o.show_decision()[0]
            ):
                raise NotFound("Opportunity not found")
            return o, await self._cluster_labels(uow, {o.run_id})

    @staticmethod
    async def _cluster_labels(uow: DiscoveryUnitOfWork, run_ids: set[UUID]) -> dict[UUID, str]:
        out: dict[UUID, str] = {}
        for rid in run_ids:
            out.update({c.id: c.label for c in await uow.clusters.list(rid)})
        return out

    async def _card(self, o: Opportunity, labels: dict[UUID, str]) -> CardView:
        visible = o.visible_claims()
        first = next((c for c in visible if c.section is ClaimSection.RATIONALE), None)
        items = {
            (e.source_type, e.ref) for c in visible if c.section is not ClaimSection.SCORE for e in c.evidence
        }
        return CardView(
            o, await self._claim_view(o, first) if first else None, len(items),
            [labels[c] for c in o.cluster_ids if c in labels],
        )  # fmt: skip

    async def _claim_view(self, o: Opportunity, c: Claim) -> ClaimView:
        async with self._uow() as uow:
            works = await uow.corpus.get_many(
                o.run_id,
                [e.label for e in c.evidence if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE)],
            )
            probes = {p.id: p for p in await uow.probes.list(o.run_id)}
        markers: list[MarkerView] = []
        for e in c.evidence:
            if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE):
                w = works.get(e.label)
                markers.append(
                    MarkerView(
                        e.id,
                        e.source_type.value,
                        work_id=e.label,
                        doi=w.doi if w else None,
                        title=w.title if w else None,
                        year=w.year if w else None,
                        authors=list(w.authors[:3]) if w else [],
                        quote=e.quote,
                    )  # fmt: skip
                )
            elif e.source_type is SourceType.COUNT:
                p = probes.get(e.probe_id) if e.probe_id else None
                markers.append(
                    MarkerView(e.id, "count", query=p.query if p else None, since_year=p.from_date.year if p else None, count=p.count if p else None)
                )  # fmt: skip
            else:
                markers.append(await self._vault_marker(o, e))
        return ClaimView(c.id, c.text, markers)

    async def _vault_marker(self, o: Opportunity, e: Evidence) -> MarkerView:
        passage = await self._vault.passage(o.owner_id, e.chunk_id) if e.chunk_id else None
        if passage is None:
            return MarkerView(e.id, "vault", document_id=e.document_id)
        mat = await self._vault.run_material(o.owner_id, (("d1", passage.document_id),))
        filename = mat[0].filename if mat and mat[0].active else None
        return MarkerView(
            e.id, "vault", document_id=passage.document_id, filename=filename, page_from=passage.page_from,
            page_to=passage.page_to, quote=e.quote if passage.retention == "keep" or e.quote else None,
            original_available=passage.retention == "keep",
        )  # fmt: skip

    # ---- evidence ------------------------------------------------------------------------------------------------
    async def open_evidence(self, owner_id: AccountId, evidence_id: UUID) -> EvidenceOpen:
        """Resolve a verified citation of a visible opportunity (vault passages are read live)."""
        async with self._uow() as uow:
            o = await uow.opportunities.find_by_evidence(evidence_id)
        if o is None or o.owner_id != owner_id or o.status not in USER_VISIBLE or not o.show_decision()[0]:
            raise NotFound("Evidence not found")
        e = next((x for c in o.visible_claims() for x in c.evidence if x.id == evidence_id), None)
        if e is None:
            raise NotFound("Evidence not found")
        if e.source_type in (SourceType.LITERATURE, SourceType.PROFILE):
            async with self._uow() as uow:
                w = (await uow.corpus.get_many(o.run_id, [e.label])).get(e.label)
            if w is None:
                raise NotFound("Evidence not found")
            return EvidenceOpen(
                e.source_type.value,
                e.id,
                w.title,
                w.year,
                w.doi,
                w.openalex_id,
                w.abstract,
                e.quote,
            )
        if e.source_type is SourceType.COUNT:
            async with self._uow() as uow:
                probes = {p.id: p for p in await uow.probes.list(o.run_id)}
            p = probes.get(e.probe_id) if e.probe_id else None
            if p is None:
                raise NotFound("Evidence not found")
            return EvidenceOpen("count", e.id, query=p.query, count=p.count, since_year=p.from_date.year)
        passage = await self._vault.passage(owner_id, e.chunk_id) if e.chunk_id else None
        if passage is None:
            raise NotFound("The source document is no longer available")
        mat = await self._vault.run_material(owner_id, (("d1", passage.document_id),))
        keep = passage.retention == "keep"
        return EvidenceOpen(
            "vault", e.id, quote=e.quote, document_id=passage.document_id,
            filename=mat[0].filename if mat else None, page_from=passage.page_from, page_to=passage.page_to,
            passage=passage.text if keep else None, original_available=keep,
        )  # fmt: skip

    # ---- admin ---------------------------------------------------------------------------------------------------
    async def admin_claims(
        self,
        actor: AccountId,
        *,
        status: ClaimStatus | None = None,
        reason: str | None = None,
        agent: str | None = None,
        prompt_version: str | None = None,
        model: str | None = None,
        run_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> Page[AdminClaimView]:
        """Claims of any status for the quality review; the access is audited."""
        limit = max(1, min(limit, MAX_PAGE))
        offset = _decode(cursor)
        async with self._uow() as uow:
            opps = await uow.opportunities.list_recent(limit, offset, run_id)
            await uow.audit.add(
                AuditEntry(
                    id=self._ids.new(),
                    actor_id=actor,
                    action="admin.claims_viewed",
                    at=self._clock.now(),
                    meta={
                        "status": status.value if status else None,
                        "reason": reason,
                        "agent": agent,
                        "run_id": str(run_id) if run_id else None,
                    },
                )  # fmt: skip
            )
            await uow.commit()
        rows = [
            AdminClaimView(o, c)
            for o in opps
            for c in o.claims
            if (status is None or c.status is status)
            and (reason is None or c.status_reason == reason)
            and (agent is None or c.agent == agent)
            and (prompt_version is None or c.prompt_version == prompt_version)
            and (model is None or c.model == model)
        ]
        return Page(rows, _encode(offset + limit) if len(opps) == limit else None)

    @property
    def admin_shows_vault_quotes(self) -> bool:
        """Whether admin claim rows may carry vault quotes (``ADMIN_CLAIMS_SHOW_VAULT_QUOTES``)."""
        return self._admin_quotes
