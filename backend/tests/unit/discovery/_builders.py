"""Small builders shared by the discovery domain tests (no logic under test lives here)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from scout.discovery.domain.budget import RunBudget
from scout.discovery.domain.enums import (
    ClaimSection,
    ClaimStatus,
    CorpusOrigin,
    OppKind,
    OppStatus,
    RunStep,
    SourceType,
)
from scout.discovery.domain.opportunity import Claim, Evidence, Opportunity, Scores
from scout.discovery.domain.run import STEP_ORDER, AnalysisRun
from scout.discovery.domain.verification import (
    ChunkMaterial,
    InsightMaterial,
    ProbeMaterial,
    RunMaterialIndex,
    WorkMaterial,
)
from scout.shared import AccountId

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
OWNER = AccountId(UUID(int=1))
OTHER_OWNER = AccountId(UUID(int=2))
NS = UUID(int=0xABCDEF)


def at(seconds: int = 0) -> datetime:
    """A deterministic timestamp."""
    return T0 + timedelta(seconds=seconds)


def uid(name: str) -> UUID:
    """A deterministic uuid for a name."""
    return uuid5(NS, name)


# ---- runs -------------------------------------------------------------------------------------------------------
def make_run(*, openalex_calls: int = 400) -> AnalysisRun:
    """A queued run."""
    return AnalysisRun(
        id=uid("run"),
        owner_id=OWNER,
        created_at=at(0),
        budget=RunBudget(usd=Decimal("1.00"), openalex_calls=openalex_calls),
    )


def running_run(step: RunStep = RunStep.PLAN_QUERIES) -> tuple[AnalysisRun, int]:
    """A running run positioned at ``step`` (built only through the public transitions) and its generation."""
    run = make_run()
    gen = run.begin_execution(at(1))
    for i, s in enumerate(STEP_ORDER):
        if s is step:
            break
        run.complete_step(s, gen, {}, at(2 + i))
    return run, gen


def done_run() -> AnalysisRun:
    """A run that completed all steps."""
    run, gen = running_run(RunStep.SCORE_PERSIST)
    run.complete_step(RunStep.SCORE_PERSIST, gen, {}, at(100))
    return run


def failed_run() -> AnalysisRun:
    """A failed run."""
    from scout.discovery.domain.enums import RunFailure

    run, _ = running_run(RunStep.RETRIEVE)
    run.fail(RunFailure.INTERNAL_ERROR, RunStep.RETRIEVE, at(100))
    return run


def cancelled_run() -> AnalysisRun:
    """A run cancelled while queued."""
    run = make_run()
    run.request_cancel(at(100))
    return run


# ---- verification index -----------------------------------------------------------------------------------------
def work(
    oid: str = "W1",
    *,
    title: str = "Lignin based foams for insulation",
    abstract: str | None = "We study lignin based foams and report their thermal conductivity in detail.",
    origin: CorpusOrigin = CorpusOrigin.SEARCH,
) -> WorkMaterial:
    """A corpus work."""
    return WorkMaterial(openalex_id=oid, title=title, abstract=abstract, origin=origin)


def keep_chunk(
    label: str = "d1.c1", text: str = "The foams must meet EN 13501 fire safety at the proposed bio-content.", *,
    owner: AccountId = OWNER, active: bool = True,
) -> ChunkMaterial:  # fmt: skip
    """A passage of a ``keep`` document."""
    return ChunkMaterial(
        label=label, chunk_id=uid(label), owner_id=owner, active=active, retention="keep", text=text
    )


def discard_chunk(
    label: str = "d2.c1", quotes: tuple[str, ...] = (), *, owner: AccountId = OWNER, active: bool = True
) -> ChunkMaterial:
    """A passage of a ``discard`` document (retained quotes only, no full text)."""
    return ChunkMaterial(
        label=label,
        chunk_id=uid(label),
        owner_id=owner,
        active=active,
        retention="discard",
        text=None,
        quotes=quotes,
    )


def probe(label: str = "n1", count: int | None = 427, *, measured: bool = True) -> ProbeMaterial:
    """A novelty probe."""
    return ProbeMaterial(label=label, query="lignin foam insulation", count=count, measured=measured)


def index(
    *,
    works: list[WorkMaterial] | None = None,
    chunks: list[ChunkMaterial] | None = None,
    insights: list[InsightMaterial] | None = None,
    probes: list[ProbeMaterial] | None = None,
    owner: AccountId = OWNER,
) -> RunMaterialIndex:
    """A material index from lists."""
    return RunMaterialIndex(
        owner_id=owner,
        works={w.openalex_id: w for w in works or []},
        chunks={c.label: c for c in chunks or []},
        insights={i.ref: i for i in insights or []},
        probes={p.label: p for p in probes or []},
    )


# ---- opportunities ----------------------------------------------------------------------------------------------
def ev(
    source: SourceType = SourceType.LITERATURE,
    ref: str = "W1",
    *,
    verified: bool | None = True,
    n: int = 0,
) -> Evidence:
    """Evidence whose id column matches its source type."""
    e = Evidence(
        id=uid(f"ev:{source}:{ref}:{n}"), ord=n, source_type=source, label=ref, ref=ref, verified=verified
    )
    if source in (SourceType.LITERATURE, SourceType.PROFILE):
        e.corpus_work_id = uid(f"work:{ref}")
    elif source is SourceType.VAULT:
        e.chunk_id = uid(f"chunk:{ref}")
    else:
        e.probe_id = uid(f"probe:{ref}")
    return e


def claim(
    section: ClaimSection = ClaimSection.RATIONALE,
    status: ClaimStatus = ClaimStatus.VERIFIED,
    evidence: list[Evidence] | None = None,
    *,
    n: int = 0,
) -> Claim:
    """A claim."""
    return Claim(
        id=uid(f"claim:{n}"), ord=n, section=section, text="a claim", status=status, evidence=evidence or []
    )


def scores(novelty: float = 0.2) -> Scores:
    """Valid scores."""
    return Scores(fit=0.5, novelty=novelty, readiness=0.5, effort=0.5, rank=0.5)


def opp(
    claims: list[Claim] | None = None, *, with_scores: bool = True, status: OppStatus = OppStatus.DRAFT
) -> Opportunity:
    """An opportunity."""
    return Opportunity(
        id=uid("opp"), run_id=uid("run"), owner_id=OWNER, batch=0, ord=0, kind=OppKind.WHITE_SPOT, title="t", one_line="o",
        created_at=at(0), status=status, scores=scores() if with_scores else None, claims=claims or [],
    )  # fmt: skip


def showable_claims() -> list[Claim]:
    """Claims that satisfy the whole show rule: literature + vault on a rationale, a count on a score claim."""
    return [
        claim(ClaimSection.RATIONALE, ClaimStatus.VERIFIED, [ev(SourceType.LITERATURE, "W1"), ev(SourceType.VAULT, "d1.c1")], n=0),
        claim(ClaimSection.SCORE, ClaimStatus.VERIFIED, [ev(SourceType.COUNT, "n1")], n=1),
    ]  # fmt: skip
