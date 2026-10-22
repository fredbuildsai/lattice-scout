"""Match notifications (M6 supplies the real channel). There is deliberately no decline method."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from scout.shared import AccountId


class MatchNotifier(Protocol):
    """Best effort, after commit; implementations never raise into the use case."""

    async def proposed(self, account_id: AccountId, match_id: UUID) -> None:
        """A new proposal is waiting."""
        ...

    async def mutual(self, account_id: AccountId, match_id: UUID) -> None:
        """Both sides accepted."""
        ...
