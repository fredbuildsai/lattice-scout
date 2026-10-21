"""The run ledger: exact, crash-safe spend accounting and fencing (DECISIONS 57, 62)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from scout.discovery.domain.budget import RunSpend


@dataclass(frozen=True)
class LedgerState:
    """What a charge returns: the new totals and whether the caller must stop."""

    spend: RunSpend
    cancel_requested: bool
    superseded: bool


class RunLedgerPort(Protocol):
    """Adds to a run's totals in one short transaction of its own (never inside a step's unit of work)."""

    async def charge(
        self,
        run_id: UUID,
        generation: int,
        *,
        cost_usd: Decimal = Decimal(0),
        llm_calls: int = 0,
        openalex_calls: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
    ) -> LedgerState:
        """Add the amounts and set ``heartbeat_at``. When ``generation`` is no longer current nothing is added and
        ``superseded`` is True; ``cancel_requested`` reflects the run row."""
        ...
