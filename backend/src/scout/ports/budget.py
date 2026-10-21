"""Global spend guard: daily caps on LLM and OpenAlex spend across all users."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from scout.shared import RateLimited


class ServicePaused(RateLimited):
    """A global daily spend cap was reached: new runs wait until the next UTC day."""

    code = "service_paused"
    http_status = 503
    title = "New analyses are paused for today"


class BudgetStatus(BaseModel):
    """Today's (UTC) spend against the caps."""

    model_config = ConfigDict(frozen=True)

    llm_spent_usd: Decimal
    llm_cap_usd: Decimal
    openalex_spent_usd: Decimal
    openalex_cap_usd: Decimal

    @property
    def paused(self) -> bool:
        """True when either cap is reached."""
        return self.llm_spent_usd >= self.llm_cap_usd or self.openalex_spent_usd >= self.openalex_cap_usd


class GlobalBudgetPort(Protocol):
    """Read the global budget."""

    async def status(self) -> BudgetStatus:
        """Spend since UTC midnight (may be cached for a few seconds)."""
        ...

    async def ensure_open(self) -> None:
        """Raise ``ServicePaused`` when a cap is reached."""
        ...
