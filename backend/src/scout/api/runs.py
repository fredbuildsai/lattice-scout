"""``/runs`` endpoints: start, poll and cancel a gap-analysis run."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from scout.api.deps import current_account, get_container
from scout.api.schemas.discovery import (
    PUBLISHED_ONLY_NOTE,
    DegradedOut,
    RunCountsOut,
    RunIn,
    RunListOut,
    RunOut,
    StepOut,
    basis_of,
)
from scout.discovery.application.service import RunView
from scout.identity.domain.account import Account
from scout.shared.context import get_context

router = APIRouter(prefix="/api/v1/runs", tags=["discovery"])


def run_out(v: RunView) -> RunOut:
    """Map a run view to its DTO (codes and counts only)."""
    r = v.run
    return RunOut(
        id=r.id, status=r.status, phase=r.phase, step=r.step.value if r.step else None,
        steps=[StepOut(name=s.name, status=s.status, progress=s.progress) for s in v.steps],
        percent=r.percent(), partial=r.partial,
        degraded=[DegradedOut(step=d.step.value, reason=d.reason) for d in r.degraded],
        error_code=r.error_code, document_ids=v.document_ids, cost_usd=str(r.spend.cost_usd),
        budget_usd=str(r.budget.usd), openalex_calls=r.spend.openalex_calls, openalex_cap=r.budget.openalex_calls,
        counts=RunCountsOut(**v.counts),
        basis=basis_of(r.inputs.basis), basis_note=PUBLISHED_ONLY_NOTE if r.inputs.basis == "published_only" else None, created_at=r.created_at, started_at=r.started_at, finished_at=r.finished_at,
    )  # fmt: skip


@router.post(
    "",
    summary="Start a gap-analysis run",
    status_code=202,
    response_model=RunOut,
    responses={
        404: {"description": "A document is not yours."},
        409: {"description": "`run_in_progress` or `profile_required`."},
        422: {"description": "`document_not_ready` or `validation_failed` (at most 10 different documents)."},
        429: {"description": "`run_limit_reached` or `daily_budget_reached`."},
    },
)
async def start_run(
    body: RunIn,
    request: Request,
    response: Response,
    account: Account = Depends(current_account),  # noqa: B008
) -> RunOut:
    """The run is queued at once; poll `GET /runs/{id}` every few seconds."""
    ctx = get_context()
    session = None
    if ctx.session_id:
        try:
            session = UUID(ctx.session_id)
        except ValueError:
            session = None
    view = await get_container(request).discovery.start(account.id, body.document_ids, session)
    response.headers["Location"] = f"/api/v1/runs/{view.run.id}"
    return run_out(view)


@router.get("", summary="Your latest runs", response_model=RunListOut)
async def list_runs(request: Request, account: Account = Depends(current_account)) -> RunListOut:  # noqa: B008
    """Newest first, at most 20."""
    views = await get_container(request).discovery.list_runs(account.id, 20)
    return RunListOut(items=[run_out(v) for v in views])


@router.get(
    "/{run_id}",
    summary="One run with its progress",
    response_model=RunOut,
    responses={404: {"description": "Not your run."}},
)
async def get_run(run_id: UUID, request: Request, account: Account = Depends(current_account)) -> RunOut:  # noqa: B008
    """Status, phase, per-step progress, degradations and counts."""
    return run_out(await get_container(request).discovery.get_run(account.id, run_id))


@router.post(
    "/{run_id}/cancel",
    summary="Stop a run",
    status_code=202,
    response_model=RunOut,
    responses={404: {"description": "Not your run."}, 409: {"description": "The run already ended."}},
)
async def cancel_run(run_id: UUID, request: Request, account: Account = Depends(current_account)) -> RunOut:  # noqa: B008
    """A queued run is cancelled at once; a running one stops at its next step or model call."""
    return run_out(await get_container(request).discovery.cancel(account.id, run_id))
