"""Async SQLAlchemy engine wrapper. Creating it opens no connection."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Engine + session factory for PostgreSQL (psycopg 3, async)."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, echo=echo, pool_pre_ping=True, hide_parameters=True
        )
        self._url = url
        self._sync_engine: Engine | None = None
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    @property
    def sync_engine(self) -> Engine:
        """Small blocking engine for background writers that run in a worker thread (keeps the loop free)."""
        if self._sync_engine is None:
            self._sync_engine = create_engine(
                self._url, pool_size=2, max_overflow=0, pool_pre_ping=True, hide_parameters=True
            )
        return self._sync_engine

    async def ping(self) -> None:
        """Run ``SELECT 1``; raises if the database is unreachable."""
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
        if self._sync_engine is not None:
            self._sync_engine.dispose()
