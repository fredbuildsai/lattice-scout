"""BufferedAnalytics: events and session touches are batch-inserted in the background."""

from __future__ import annotations

from typing import Any

from sqlalchemy import MetaData, Table, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from scout.adapters.observability.buffer import BufferedWriter
from scout.adapters.persistence.db import Database
from scout.ports.analytics import AnalyticsEvent, SessionTouch


def _keep_under_pressure(evt: AnalyticsEvent) -> bool:
    """When the queue is >= 80 % full keep every page_view and 1 in 4 other events."""
    return evt.name == "page_view" or (evt.id.int % 4 == 0)


class BufferedAnalytics:
    """Implements ``AnalyticsPort``."""

    def __init__(self, db: Database, flush_ms: int, capacity: int = 20_000) -> None:
        self._db = db
        self._tables: dict[str, Table] = {}
        self._events = BufferedWriter[AnalyticsEvent](
            self._write_events, name="analytics_event", capacity=capacity, flush_ms=flush_ms,
            sample=_keep_under_pressure,
        )  # fmt: skip
        self._sessions = BufferedWriter[SessionTouch](
            self._write_sessions, name="app_session", capacity=max(capacity // 10, 100), flush_ms=flush_ms
        )

    async def _table(self, name: str) -> Table:
        if name not in self._tables:
            async with self._db.engine.connect() as conn:
                self._tables[name] = await conn.run_sync(
                    lambda sync_conn: Table(name, MetaData(), autoload_with=sync_conn)
                )
        return self._tables[name]

    async def _write_events(self, batch: list[AnalyticsEvent]) -> None:
        table = await self._table("analytics_event")
        async with self._db.engine.begin() as conn:
            await conn.execute(insert(table), [e.model_dump(mode="python") for e in batch])

    async def _write_sessions(self, batch: list[SessionTouch]) -> None:
        table = await self._table("app_session")
        merged: dict[Any, SessionTouch] = {}
        for s in batch:
            merged[s.session_id] = s  # last touch of a session within the batch wins
        rows = [
            {
                "id": s.session_id, "account_id": s.account_id, "started_at": s.seen_at, "last_seen_at": s.seen_at,
                "user_agent_family": s.user_agent_family, "device_type": s.device_type, "viewport": s.viewport,
                "app_version": s.app_version, "entry_page": s.entry_page, "referrer_domain": s.referrer_domain,
            }
            for s in merged.values()
        ]  # fmt: skip
        stmt = pg_insert(table).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[table.c.id],
            set_={
                "last_seen_at": func.greatest(table.c.last_seen_at, stmt.excluded.last_seen_at),
                "account_id": func.coalesce(stmt.excluded.account_id, table.c.account_id),
                "updated_at": func.now(),
            },
        )
        async with self._db.engine.begin() as conn:
            await conn.execute(stmt)

    def track(self, evt: AnalyticsEvent) -> None:
        """Buffer one event (never raises)."""
        self._events.offer(evt)

    def touch_session(self, s: SessionTouch) -> None:
        """Buffer a session upsert (never raises)."""
        self._sessions.offer(s)

    async def start(self) -> None:
        """Start the flush loops."""
        self._sessions.start()
        self._events.start()

    async def close(self) -> None:
        """Flush sessions first (events reference them logically), then events."""
        await self._sessions.close()
        await self._events.close()


class NullAnalytics:
    """Used when ``ANALYTICS_ENABLED=false``."""

    def track(self, evt: AnalyticsEvent) -> None:
        """Drop."""

    def touch_session(self, s: SessionTouch) -> None:
        """Drop."""
