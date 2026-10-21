"""SQL audit log (same session/transaction as the change)."""

from __future__ import annotations

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence.models import audit_log
from scout.ports.audit import AuditEntry


class SqlAuditLog:
    """Implements ``AuditLog`` on an open session."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, entry: AuditEntry) -> None:
        """Insert an audit row."""
        await self._s.execute(
            insert(audit_log).values(
                id=entry.id,
                actor_id=entry.actor_id,
                action=entry.action,
                target=entry.target,
                at=entry.at,
                meta=entry.meta,
            )
        )
