"""Async SQLAlchemy engine wrapper. Creating it opens no connection."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


class Database:
    """Engine + session factory for PostgreSQL (psycopg 3, async)."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(url, echo=echo, pool_pre_ping=True)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def ping(self) -> None:
        """Run ``SELECT 1``; raises if the database is unreachable."""
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self.engine.dispose()
