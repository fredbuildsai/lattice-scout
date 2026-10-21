"""DTOs of the discovery endpoints. Only verified claims and citations are ever returned."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RunIn(BaseModel):
    """Start a run."""

    document_ids: list[UUID] = Field(
        default_factory=list, max_length=10, description="Vault documents to include."
    )


class StepOut(BaseModel):
    """One step of the progress list."""

    name: str
    status: Literal["pending", "running", "done", "degraded"]
    progress: float = Field(ge=0, le=1)


class DegradedOut(BaseModel):
    """A step that finished with less than full output."""

    step: str
    reason: str


class RunCountsOut(BaseModel):
    """What the run found so far."""

    corpus: int
    clusters: int
    candidates: int
    drafts: int
    shown: int


def basis_of(value: object) -> Literal["full", "published_only"]:
    """Normalise a stored basis (anything unknown reads as ``full``)."""
    return "published_only" if value == "published_only" else "full"


PUBLISHED_ONLY_NOTE = (
    "Based on your published work only; your uploaded documents were not used because no approved provider "
    "is configured."
)


class RunOut(BaseModel):
    """A run. Poll it every 3 seconds while ``status`` is ``queued`` or ``running``."""

    id: UUID
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    phase: str
    step: str | None
    steps: list[StepOut]
    percent: int = Field(ge=0, le=100)
    partial: bool
    degraded: list[DegradedOut]
    error_code: str | None
    document_ids: list[UUID]
    cost_usd: str
    budget_usd: str
    openalex_calls: int
    openalex_cap: int
    counts: RunCountsOut
    basis: Literal["full", "published_only"] = Field(
        default="full", description="`published_only`: confidential vault material was left out of this run."
    )
    basis_note: str | None = Field(
        default=None, description="Plain-language note shown when `basis` is `published_only`."
    )
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunListOut(BaseModel):
    """Latest runs."""

    items: list[RunOut]


class ScoresOut(BaseModel):
    """Opportunity scores in [0, 1]."""

    fit: float
    novelty: float
    readiness: float
    effort: float


class NoveltyOut(BaseModel):
    """The measured count behind the novelty score."""

    count: int | None
    since_year: int | None
    query: str | None
    band: str | None


class EvidenceOut(BaseModel):
    """A verified citation. ``source_type`` says which fields are set."""

    id: UUID
    source_type: Literal["literature", "profile", "vault", "count"]
    work_id: str | None = None
    doi: str | None = None
    title: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    quote: str | None = None
    document_id: UUID | None = None
    filename: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    original_available: bool = False
    query: str | None = None
    since_year: int | None = None
    count: int | None = None


class ClaimOut(BaseModel):
    """A verified claim and the citations that carry it."""

    id: UUID
    text: str
    markers: list[EvidenceOut]


class OpportunityCardOut(BaseModel):
    """List item."""

    id: UUID
    run_id: UUID
    kind: Literal["white_spot", "synergy", "resubmission"]
    title: str
    why: ClaimOut | None
    scores: ScoresOut | None
    novelty: NoveltyOut
    status: Literal["shown", "saved", "dismissed"]
    verified_sources: int
    clusters: list[str]
    basis: Literal["full", "published_only"] = "full"
    basis_note: str | None = None
    created_at: datetime


class OpportunityListOut(BaseModel):
    """A page of opportunity cards."""

    items: list[OpportunityCardOut]
    next_cursor: str | None


class ChecklistItemOut(BaseModel):
    """A readiness item."""

    item: str
    present: bool


class OpportunityDetailOut(OpportunityCardOut):
    """Detail page."""

    rationale: list[ClaimOut]
    objection: list[ClaimOut]
    score_claims: list[ClaimOut]
    next_steps: list[str]
    readiness: list[ChecklistItemOut]
    effort_estimate: str | None


class OpportunityPatchIn(BaseModel):
    """The owner's choice."""

    status: Literal["shown", "saved", "dismissed"]


class EvidenceOpenOut(BaseModel):
    """The source behind a citation, resolved live."""

    kind: Literal["literature", "profile", "vault", "count"]
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


class AdminEvidenceOut(BaseModel):
    """Evidence row of the quality review."""

    source_type: str
    label: str
    ref: str
    verified: bool | None
    check: str | None
    quote: str | None


class AdminClaimOut(BaseModel):
    """Claim row of the quality review (any status)."""

    claim_id: UUID
    opportunity_id: UUID
    run_id: UUID
    section: str
    text: str
    status: str
    status_reason: str | None
    set_by: str
    agent: str | None
    prompt_version: str | None
    model: str | None
    critic_verdict: str | None
    critic_reason: str | None
    opportunity_status: str
    evidence: list[AdminEvidenceOut]


class AdminClaimListOut(BaseModel):
    """A page of claims."""

    items: list[AdminClaimOut]
    next_cursor: str | None


Limit = Annotated[int, Field(ge=1, le=50)]
