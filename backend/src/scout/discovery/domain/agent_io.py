"""Typed inputs and outputs of the five discovery agents.

Everything a model returns is cleaned (control characters stripped) and bounded here, before any code or database
sees it: oversize strings are cut and oversize lists truncated rather than rejected, so one chatty answer does not
fail a paid call. Input models carry only what an agent may see (no author names, no private quotes beyond the
bounded passages).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from scout.shared.confidential import (
    AgentInput,
    confidential_field,
    derived_field,
    mixed_items_field,
    published_field,
)
from scout.shared.text import clean_text


def _cap(n: int) -> BeforeValidator:
    return BeforeValidator(lambda v: clean_text(v, n) if isinstance(v, str) else v)


S30 = Annotated[str, _cap(30)]
S40 = Annotated[str, _cap(40)]
S60 = Annotated[str, _cap(60)]
S80 = Annotated[str, _cap(80)]
S120 = Annotated[str, _cap(120)]
S150 = Annotated[str, _cap(150)]
S200 = Annotated[str, _cap(200)]
S300 = Annotated[str, _cap(300)]
S600 = Annotated[str, _cap(600)]


def _trim(n: int) -> BeforeValidator:
    """Keep the first ``n`` items of a list."""
    return BeforeValidator(lambda v: v[:n] if isinstance(v, list) else v)


class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---- QueryPlanner -------------------------------------------------------------------------------------------------
class PlanTopic(BaseModel):
    """A profile topic the planner may build queries around."""

    id: str
    name: S80 | None = None
    weight: Annotated[float, AfterValidator(lambda v: round(v, 3))] = 0.0


class PlanInsight(BaseModel):
    """A verified vault insight (summary only)."""

    insight_ref: str
    section: str
    text: S300


class PlanField(BaseModel):
    """The launch field (``config/fields.yaml``)."""

    name: str
    openalex_topic_ids: list[str] = []
    keywords: list[str] = []


class QueryPlannerInput(AgentInput):
    """What the QueryPlanner sees. No names, no quotes, no chunks. ``insights`` and ``keywords`` would come from the
    vault, but the pipeline leaves them empty: the planned search strings go to OpenAlex (ADR 016)."""

    topics: list[PlanTopic] = published_field()
    recent_titles: list[S200] = published_field(default_factory=list, max_length=20)
    insights: list[PlanInsight] = confidential_field(default_factory=list, max_length=60)
    keywords: list[S40] = confidential_field(default_factory=list, max_length=30)
    field: PlanField = published_field()
    year_from: int = published_field()


class PlannedQuery(_Out):
    """One literature search."""

    search: S80
    purpose: Literal["core", "adjacent", "objection", "bridge"] = "core"
    because: S80 = ""
    max_results: Annotated[
        int, BeforeValidator(lambda v: max(1, min(200, int(v))) if isinstance(v, int | float) else 50)
    ] = 50


class QueryPlan(_Out):
    """Typed output of the QueryPlanner: 1 to 25 queries."""

    queries: Annotated[list[PlannedQuery], _trim(25)] = Field(min_length=1)


# ---- ClusterLabeler -----------------------------------------------------------------------------------------------
class TopicName(BaseModel):
    """An OpenAlex topic shared by the works of a cluster."""

    id: str
    name: S80


class ClusterLabelInput(AgentInput):
    """What the ClusterLabeler sees: topic names and up to ten published titles."""

    cluster_id: str = published_field()
    openalex_topics: list[TopicName] = published_field(max_length=10)
    sample_titles: list[S200] = published_field(max_length=10)


class ClusterLabel(_Out):
    """Typed output of the ClusterLabeler. ``search_terms`` build the novelty probes."""

    cluster_id: S30
    label: S60
    description: S200 = ""
    search_terms: Annotated[list[S30], _trim(5)] = Field(default_factory=list)


# ---- InsightTopicTagger -------------------------------------------------------------------------------------------
class TagCluster(BaseModel):
    """A cluster as the tagger sees it."""

    cluster_id: str
    label: S60
    description: S200 = ""


class TagInsight(BaseModel):
    """A verified insight to place on the map."""

    insight_ref: str
    text: S300


class InsightTopicTaggerInput(AgentInput):
    """What the InsightTopicTagger sees (at most 40 insights per call). Wholly confidential: no published form."""

    clusters: list[TagCluster] = published_field(max_length=30)
    insights: list[TagInsight] = confidential_field(max_length=40)


class InsightTag(_Out):
    """One insight placed on 0 to 3 clusters."""

    insight_ref: S40
    cluster_ids: Annotated[list[S30], _trim(3)] = Field(default_factory=list)
    strength: Literal["core", "partial"] = "partial"
    reason: S150 = ""


class InsightTopicTags(_Out):
    """Typed output of the InsightTopicTagger."""

    tags: list[InsightTag] = Field(default_factory=list)


# ---- GapAnalyst ---------------------------------------------------------------------------------------------------
class RecentWork(BaseModel):
    """One of the researcher's own recent works."""

    work_id: str
    title: S200
    year: int | None = None


class ProfileSummary(BaseModel):
    """Public profile data for the analyst."""

    topics: list[S80] = Field(max_length=25)
    recent_works: list[RecentWork] = Field(max_length=15)


class InsightForAnalyst(BaseModel):
    """A verified vault insight."""

    insight_ref: str
    document: str
    section: str
    text: S300


class CoverageRow(BaseModel):
    """Coverage of one candidate cluster."""

    cluster_id: str
    label: S60
    corpus_count: int
    user_works: int
    user_insights: Annotated[float, AfterValidator(lambda v: round(v, 3))]
    density: Annotated[float, AfterValidator(lambda v: round(v, 3))]


class NoveltyRow(BaseModel):
    """A measured novelty probe."""

    candidate_id: str
    cluster_id: str
    query: S200
    since_year: int
    count: int


class PaperForAnalyst(BaseModel):
    """A corpus paper the analyst may cite as a ``work``."""

    work_id: str
    title: S200
    year: int | None = None
    abstract: Annotated[str, _cap(1200)] = ""


class ChunkForAnalyst(BaseModel):
    """A vault passage the analyst may quote (``keep``: the text; ``discard``: the retained quotes)."""

    chunk_id: str
    document: str
    page: int
    text: Annotated[str, _cap(4000)]


class GapContext(AgentInput):
    """Everything the GapAnalyst sees for one batch of candidates. Insights and chunks come from the vault."""

    profile_summary: ProfileSummary = published_field()
    insights: list[InsightForAnalyst] = confidential_field(default_factory=list)
    coverage: list[CoverageRow] = published_field()
    novelty: list[NoveltyRow] = published_field()
    papers: list[PaperForAnalyst] = published_field(max_length=40)
    chunks: list[ChunkForAnalyst] = confidential_field(default_factory=list, max_length=40)
    max_opportunities: int = published_field(default=5)
    exclude_titles: list[S120] = derived_field(
        default_factory=list, max_length=40
    )  # earlier titles: provenance


class EvidenceRefOut(_Out):
    """A citation the analyst wrote: ``type`` is work, chunk, insight or count."""

    type: Literal["work", "chunk", "insight", "count"]
    id: S40
    quote: S600 | None = None


class ClaimDraft(_Out):
    """A statement with its citations."""

    text: S300
    section: Literal["rationale", "objection"] = "rationale"
    evidence_refs: Annotated[list[EvidenceRefOut], _trim(4)] = Field(default_factory=list)


class ChecklistItem(_Out):
    """What the researcher needs, and whether the vault shows they have it."""

    item: S120
    present_in_vault: bool = False
    ref: S40 | None = None


class OpportunityDraft(_Out):
    """One candidate opportunity."""

    kind: Literal["white_spot", "synergy", "resubmission"] = "white_spot"
    title: S120
    one_line: S200 = ""
    cluster_ids: Annotated[list[S30], _trim(3)] = Field(default_factory=list)
    claims: Annotated[list[ClaimDraft], _trim(8)] = Field(min_length=1)
    readiness_checklist: Annotated[list[ChecklistItem], _trim(8)] = Field(default_factory=list)
    effort_estimate: Literal["low", "medium", "high"] = "medium"
    next_steps: Annotated[list[S120], _trim(3)] = Field(default_factory=list)


class GapAnalysis(_Out):
    """Typed output of the GapAnalyst: at most five opportunities per call."""

    opportunities: Annotated[list[OpportunityDraft], _trim(5)] = Field(default_factory=list)


# ---- Critic -------------------------------------------------------------------------------------------------------
class CriticClaim(BaseModel):
    """A claim with the result of the code checks."""

    index: int
    text: S300
    check_status: str
    evidence: list[str] = []


class CriticDraft(BaseModel):
    """The draft under review."""

    title: S120
    kind: str
    claims: list[CriticClaim]


class EvidenceText(BaseModel):
    """Resolved text behind a citation (an abstract, a passage, a count)."""

    ref_id: str
    type: str
    title: S200 | None = None
    year: int | None = None
    text: Annotated[str, _cap(1200)]

    @property
    def is_confidential(self) -> bool:
        """Passages and insights come from the vault; works and counts are published."""
        return self.type not in ("work", "count")


class SimilarPaper(BaseModel):
    """A corpus paper close to the draft's title."""

    work_id: str
    title: S200
    year: int | None = None
    abstract: Annotated[str, _cap(800)] = ""


class CriticInput(AgentInput):
    """What the Critic sees: one draft and only its evidence. The draft's text is derived from whatever the
    analyst saw: when the run used vault material (``tainted``) the draft is confidential, so the call goes to an
    authorized endpoint or not at all; passages and insights are withheld when none exists."""

    draft: CriticDraft = (
        derived_field()
    )  # written by an analyst that saw the vault: confidential when tainted
    evidence_texts: list[EvidenceText] = mixed_items_field()
    similar_papers: list[SimilarPaper] = published_field(default_factory=list, max_length=5)


class ClaimVerdict(_Out):
    """The Critic's judgement of one claim."""

    index: int
    status: Literal["verified", "unverified", "contradicted"]
    reason: S150 | None = None
    fix: S300 | None = None


class CriticVerdict(_Out):
    """Typed output of the Critic."""

    claims: list[ClaimVerdict] = Field(default_factory=list)
    already_done: bool = False
    already_done_by: S80 | None = None
    keep: bool = False
    reasons: Annotated[list[S150], _trim(3)] = Field(default_factory=list)
