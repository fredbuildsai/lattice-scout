"""Liveness and readiness endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from scout.bootstrap import Container
from scout.shared import InfrastructureError

router = APIRouter(tags=["health"])


class Status(BaseModel):
    """Simple status body."""

    status: str


@router.get("/health", summary="Liveness probe", response_model=Status)
async def health() -> Status:
    """Return 200 while the process is up."""
    return Status(status="ok")


@router.get("/ready", summary="Readiness probe (pings the database)", response_model=Status)
async def ready(request: Request) -> Status:
    """Return 200 when the database answers, 503 otherwise."""
    container: Container = request.app.state.container
    try:
        await container.db.ping()
    except Exception as exc:
        raise InfrastructureError("Database unreachable") from exc
    return Status(status="ready")
