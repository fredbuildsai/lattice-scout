"""BufferedUsageMeter: LLM and external call records are batch-inserted in the background."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import MetaData, Table, insert

from scout.adapters.observability.buffer import BufferedWriter
from scout.adapters.persistence.db import Database
from scout.ports.usage import ExternalCallRecord, LlmCallRecord


def _rows(items: list[Any]) -> list[Mapping[str, Any]]:
    return [i.model_dump(mode="python") for i in items]


class BufferedUsageMeter:
    """Implements ``UsageMeterPort``; call ``start()`` / ``close()`` through the container lifecycle."""

    def __init__(self, db: Database, flush_ms: int, capacity: int = 10_000) -> None:
        self._db = db
        self._tables: dict[str, Table] = {}
        self._llm = BufferedWriter[LlmCallRecord](
            self._write_llm, name="llm_call", capacity=capacity, flush_ms=flush_ms
        )
        self._ext = BufferedWriter[ExternalCallRecord](
            self._write_ext, name="external_call", capacity=capacity, flush_ms=flush_ms
        )

    async def _table(self, name: str) -> Table:
        if name not in self._tables:
            async with self._db.engine.connect() as conn:
                self._tables[name] = await conn.run_sync(
                    lambda sync_conn: Table(name, MetaData(), autoload_with=sync_conn)
                )
        return self._tables[name]

    async def _write_llm(self, batch: list[LlmCallRecord]) -> None:
        table = await self._table("llm_call")
        async with self._db.engine.begin() as conn:
            await conn.execute(insert(table), _rows(batch))

    async def _write_ext(self, batch: list[ExternalCallRecord]) -> None:
        table = await self._table("external_call")
        async with self._db.engine.begin() as conn:
            await conn.execute(insert(table), _rows(batch))

    def record_llm_call(self, rec: LlmCallRecord) -> None:
        """Buffer one LLM call (never raises)."""
        self._llm.offer(rec)

    def record_external_call(self, rec: ExternalCallRecord) -> None:
        """Buffer one external call (never raises)."""
        self._ext.offer(rec)

    async def start(self) -> None:
        """Start the flush loops."""
        self._llm.start()
        self._ext.start()

    async def close(self) -> None:
        """Flush and stop."""
        await self._llm.close()
        await self._ext.close()
