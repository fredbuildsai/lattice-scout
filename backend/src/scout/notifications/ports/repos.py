"""Notification ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from scout.notifications.domain.notification import Prefs, Template


class EmailSender(Protocol):
    """Send one plain-text mail. Raises ``InfrastructureError`` when the provider fails."""

    async def send(self, to: str, subject: str, body: str) -> None:
        """Deliver."""
        ...


class NotificationStore(Protocol):
    """Preferences, recipients and the delivery log."""

    async def get_prefs(self, account_id: UUID) -> Prefs:
        """Preferences (defaults when never saved)."""
        ...

    async def save_prefs(self, account_id: UUID, prefs: Prefs, now: datetime) -> None:
        """Upsert."""
        ...

    async def recipient(self, account_id: UUID) -> str | None:
        """E-mail of an active account, else None."""
        ...

    async def was_sent(self, account_id: UUID, template: Template, ref: str) -> bool:
        """Whether this (account, template, ref) already went out."""
        ...

    async def record(self, account_id: UUID, template: Template, ref: str, now: datetime) -> None:
        """Log a delivery (idempotent)."""
        ...

    async def digest_recipients(self) -> list[UUID]:
        """Active accounts that opted in to the digest."""
        ...
