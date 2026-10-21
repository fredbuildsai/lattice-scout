"""Global daily budget guard over the usage tables, with alerts through the error log."""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import structlog
from sqlalchemy import text

from scout.adapters.persistence.db import Database
from scout.ports.budget import BudgetStatus, ServicePaused
from scout.shared import Clock

log = structlog.get_logger()
WARN_AT = Decimal("0.8")


class SpendReader(Protocol):
    """Sum of recorded costs since a point in time."""

    async def spent_since(self, since: datetime) -> tuple[Decimal, Decimal]:
        """(LLM spend, OpenAlex spend) in USD."""
        ...


class SqlSpendReader:
    """Reads ``llm_call`` and ``external_call``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def spent_since(self, since: datetime) -> tuple[Decimal, Decimal]:
        """Sum the cost columns of today's rows."""
        async with self._db.sessionmaker() as s:
            llm = (
                await s.execute(
                    text("SELECT coalesce(sum(cost_usd), 0) FROM llm_call WHERE ts >= :t"), {"t": since}
                )
            ).scalar()
            oa = (
                await s.execute(
                    text(
                        "SELECT coalesce(sum(cost_usd), 0) FROM external_call WHERE service = 'openalex' AND ts >= :t"
                    ),
                    {"t": since},
                )
            ).scalar()
        return Decimal(llm or 0), Decimal(oa or 0)


class GlobalBudget:
    """``GlobalBudgetPort``: status cached for ``ttl_s`` seconds; logs ``budget.warning`` at 80% and
    ``budget.cap_reached`` at 100%, once per service and UTC day (the alert rule watches these events)."""

    def __init__(
        self, reader: SpendReader, clock: Clock, llm_cap: Decimal, openalex_cap: Decimal, ttl_s: float = 30.0
    ) -> None:
        self._reader, self._clock, self._llm_cap, self._oa_cap, self._ttl = (
            reader,
            clock,
            llm_cap,
            openalex_cap,
            ttl_s,
        )
        self._cached: tuple[float, BudgetStatus] | None = None
        self._alerted: set[tuple[str, str, str]] = set()

    async def status(self) -> BudgetStatus:
        """Today's spend against the caps."""
        now = time.monotonic()
        if self._cached and now - self._cached[0] < self._ttl:
            return self._cached[1]
        midnight = self._clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
        llm, oa = await self._reader.spent_since(midnight)
        st = BudgetStatus(
            llm_spent_usd=llm, llm_cap_usd=self._llm_cap, openalex_spent_usd=oa, openalex_cap_usd=self._oa_cap
        )
        self._cached = (now, st)
        self._alert(str(midnight.date()), "llm", llm, self._llm_cap)
        self._alert(str(midnight.date()), "openalex", oa, self._oa_cap)
        return st

    def _alert(self, day: str, service: str, spent: Decimal, cap: Decimal) -> None:
        if cap <= 0:
            return
        for level, threshold in (("cap_reached", cap), ("warning", cap * WARN_AT)):
            if spent >= threshold and (day, service, level) not in self._alerted:
                self._alerted.add((day, service, level))
                emit = log.error if level == "cap_reached" else log.warning
                emit(f"budget.{level}", service=service, spent_usd=str(spent), cap_usd=str(cap), day=day)

    async def ensure_open(self) -> None:
        """Raise ``ServicePaused`` when either cap is reached."""
        if (await self.status()).paused:
            raise ServicePaused("New analyses are paused until tomorrow (UTC)")
