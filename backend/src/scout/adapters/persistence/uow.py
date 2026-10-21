"""Base class for SQL units of work: one ``AsyncSession`` per use case, rollback unless committed."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence.db import Database


class SqlUnitOfWork:
    """Opens a session on enter; closes (rolling back anything uncommitted) on exit."""

    session: AsyncSession

    def __init__(self, db: Database) -> None:
        self._db = db

    async def __aenter__(self) -> Self:
        self.session = self._db.sessionmaker()
        self._bind()
        return self

    def _bind(self) -> None:
        """Create repositories on ``self.session`` (overridden)."""

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        """Commit the transaction."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Roll back the transaction."""
        await self.session.rollback()
