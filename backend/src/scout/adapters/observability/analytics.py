"""BufferedAnalytics: events and session touches are batch-inserted in the background."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import psycopg
from sqlalchemy import MetaData, Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from scout.adapters.observability.buffer import BufferedWriter
from scout.adapters.persistence.db import Database
from scout.ports.analytics import AnalyticsEvent, SessionTouch

_EVENT_COLUMNS = "id, ts, account_id, session_id, name, page, prev_page, dwell_ms, properties, client_ts, seq"


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

    def _table(self, name: str) -> Table:
        if name not in self._tables:
            self._tables[name] = Table(name, MetaData(), autoload_with=self._db.sync_engine)
        return self._tables[name]

    def _insert_events(self, batch: list[AnalyticsEvent]) -> None:
        """Bulk load with COPY (mostly C, so the event loop is hardly disturbed by the GIL)."""
        rows = [
            (
                e.id, e.ts, e.account_id, e.session_id, e.name, e.page, e.prev_page, e.dwell_ms,
                json.dumps(e.properties), e.client_ts, e.seq,
            )
            for e in batch
        ]  # fmt: skip
        pooled = self._db.sync_engine.raw_connection()
        conn: psycopg.Connection[Any] = pooled.driver_connection  # type: ignore[assignment]
        try:
            with (
                conn.cursor() as cur,
                cur.copy(f"COPY analytics_event ({_EVENT_COLUMNS}) FROM STDIN") as copy,
            ):
                for row in rows:
                    copy.write_row(row)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            pooled.close()

    def _upsert_sessions(self, batch: list[SessionTouch]) -> None:
        table = self._table("app_session")
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
        with self._db.sync_engine.begin() as conn:
            conn.execute(stmt)

    async def _write_events(self, batch: list[AnalyticsEvent]) -> None:
        await asyncio.to_thread(self._insert_events, batch)

    async def _write_sessions(self, batch: list[SessionTouch]) -> None:
        await asyncio.to_thread(self._upsert_sessions, batch)

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
