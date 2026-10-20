"""PostgreSQL response cache (``openalex_cache`` table, 7-day TTL)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from scout.adapters.persistence.db import Database
from scout.shared import Clock

from .openalex import CACHE_TTL


class PostgresOpenAlexCache:
    """Implements ``ResponseCache``."""

    def __init__(self, db: Database, clock: Clock) -> None:
        self._db, self._clock = db, clock

    async def get(self, key: str) -> dict[str, Any] | None:
        """Fresh response or None."""
        async with self._db.engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT response FROM openalex_cache WHERE key = :k AND fetched_at > :min"),
                    {"k": key, "min": self._clock.now() - CACHE_TTL},
                )
            ).first()
        return dict(row[0]) if row else None

    async def put(self, key: str, response: dict[str, Any]) -> None:
        """Upsert."""
        async with self._db.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO openalex_cache (key, response, fetched_at) VALUES (:k, CAST(:r AS jsonb), :now) "
                    "ON CONFLICT (key) DO UPDATE SET response = EXCLUDED.response, fetched_at = EXCLUDED.fetched_at"
                ),
                {"k": key, "r": json.dumps(response), "now": self._clock.now()},
            )
