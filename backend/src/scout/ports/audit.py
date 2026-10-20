"""Audit log port: security-relevant actions, written inside the caller's unit of work."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue


class AuditEntry(BaseModel):
    """One ``audit_log`` row."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    actor_id: UUID | None
    action: str
    target: str | None = None
    at: AwareDatetime
    meta: dict[str, JsonValue] = {}


class AuditLog(Protocol):
    """Append-only audit trail (same transaction as the change it records)."""

    async def add(self, entry: AuditEntry) -> None:
        """Record an entry."""
        ...
