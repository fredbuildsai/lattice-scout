"""Logging notifier: M6 replaces it with the e-mail channel. No decline method exists, by design."""

from __future__ import annotations

from uuid import UUID

import structlog

from scout.shared import AccountId

log = structlog.get_logger()


class LoggingMatchNotifier:
    """Writes one log line per notification."""

    async def proposed(self, account_id: AccountId, match_id: UUID) -> None:
        """A new proposal is waiting."""
        log.info("match_proposed", account_id=str(account_id), match_id=str(match_id))

    async def mutual(self, account_id: AccountId, match_id: UUID) -> None:
        """Both sides accepted."""
        log.info("match_mutual", account_id=str(account_id), match_id=str(match_id))
