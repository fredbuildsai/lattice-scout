"""``/profile`` endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from scout.api.deps import current_account, get_container
from scout.api.schemas.profile import (
    CandidateGroupOut,
    CandidatesOut,
    ClaimIn,
    ImportOut,
    OpenAlexIdOut,
    ProfileOut,
    RecordOut,
    TopicOut,
    WorkOut,
    WorkPatchIn,
    WorksSummaryOut,
)
from scout.identity.domain.account import Account
from scout.profile.application.service import ProfileView
from scout.profile.domain.grouping import CandidateGroup, GroupedRecord
from scout.profile.domain.researcher import Researcher, VerifiedLevel
from scout.profile.domain.work import ClaimedWork
from scout.shared.context import get_context

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


def public_level(r: Researcher) -> str:
    """User-facing level: ``contested`` stays internal so a claim never reveals that someone else holds the id."""
    level = r.level_without_conflict() if r.verified_level == VerifiedLevel.CONTESTED else r.verified_level
    return level.value


def _record(r: GroupedRecord) -> RecordOut:
    s = r.snapshot
    return RecordOut(
        author_id=s.id, display_name=s.display_name, orcid=s.orcid, works_count=s.works_count,
        institutions=list(s.institutions), basis=r.basis.value,
    )  # fmt: skip


def _group(g: CandidateGroup) -> CandidateGroupOut:
    primary = next(r for r in g.records if r.snapshot.id == g.primary.id)
    return CandidateGroupOut(
        group_id=g.primary.id, primary=_record(primary), records=[_record(r) for r in g.records],
        possible=[_record(r) for r in g.possible], includes_records=g.includes_records, works_count=g.works_count,
        cited_by_count=g.cited_by_count, orcid=g.orcid, institutions=list(g.institutions), country=g.primary.country,
        top_topics=[TopicOut(topic_id=t.id, name=t.name, subfield=t.subfield, weight=t.weight) for t in g.top_topics()],
    )  # fmt: skip


def _work_out(w: ClaimedWork) -> WorkOut:
    return WorkOut(
        id=w.id, openalex_work_id=w.openalex_work_id, doi=w.doi, title=w.title, year=w.year, cited_by=w.cited_by,
        type=w.type, included=w.included, included_by_user=w.included_by_user,
        uncertain_reason=w.uncertain_reason.value if w.uncertain_reason else None,
    )  # fmt: skip


def _profile_out(r: Researcher, works: list[ClaimedWork]) -> ProfileOut:
    return ProfileOut(
        researcher_id=r.id, display_name=r.display_name, orcid=r.orcid, institution=r.institution, country=r.country,
        verified_level=public_level(r),
        import_=ImportOut(
            status=r.import_status.value, generation=r.import_generation, error_code=r.import_error,
            requested_at=r.import_requested_at, finished_at=r.imported_at,
        ),
        openalex_ids=[
            OpenAlexIdOut(author_id=x.author_id, merged=x.merged, basis=x.basis.value, works_count=x.works_count)
            for x in r.openalex_ids
        ],
        topics=[TopicOut(topic_id=t.topic_id, name=t.name, subfield=t.subfield, weight=t.weight) for t in r.topics],
        works_summary=WorksSummaryOut(
            total=len(works), included=sum(w.included for w in works),
            uncertain=sum(w.uncertain_reason is not None for w in works),
        ),
        works=[_work_out(w) for w in works],
    )  # fmt: skip


@router.get(
    "/candidates",
    summary="Find OpenAlex author records for a name (split profiles grouped)",
    response_model=CandidatesOut,
)
async def candidates(
    request: Request,
    name: str = Query(min_length=2, max_length=200, description="Researcher name."),
    affiliation: str | None = Query(default=None, max_length=200, description="Institution hint."),
    _: Account = Depends(current_account),  # noqa: B008
) -> CandidatesOut:
    """Search OpenAlex; records sharing an ORCID are returned as one card ("includes N records")."""
    groups = await get_container(request).profile.find_candidates(name, affiliation)
    return CandidatesOut(groups=[_group(g) for g in groups])


@router.post(
    "/claim",
    summary="Claim OpenAlex records as your profile and start the import",
    status_code=202,
    response_model=ProfileOut,
)
async def claim(request: Request, body: ClaimIn, account: Account = Depends(current_account)) -> ProfileOut:  # noqa: B008
    """Idempotent: claiming the same records again returns the same import generation."""
    r = await get_container(request).profile.claim(
        account.id, body.author_ids, body.primary_author_id, get_context().session_id
    )
    return _profile_out(r, [])


@router.get(
    "",
    summary="Your researcher profile, claimed works and import state",
    response_model=ProfileOut,
    response_model_by_alias=True,
)
async def get_profile(request: Request, account: Account = Depends(current_account)) -> ProfileOut:  # noqa: B008
    """Poll this while ``import.status`` is queued or importing."""
    view: ProfileView = await get_container(request).profile.get_profile(account.id)
    return _profile_out(view.researcher, view.works)


@router.patch("/works/{work_id}", summary="Include or exclude one of your works", response_model=WorkOut)
async def patch_work(
    work_id: UUID,
    body: WorkPatchIn,
    request: Request,
    account: Account = Depends(current_account),  # noqa: B008
) -> WorkOut:
    """Another user's work id answers 404."""
    w = await get_container(request).profile.toggle_work(account.id, work_id, body.included)
    return _work_out(w)
