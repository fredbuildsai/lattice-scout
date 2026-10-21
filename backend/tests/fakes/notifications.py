"""In-memory notification store and sender."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from scout.notifications.domain.notification import Prefs, Template
from scout.shared import InfrastructureError


class FakeNotificationStore:
    def __init__(self) -> None:
        self.prefs: dict[UUID, Prefs] = {}
        self.emails: dict[UUID, str] = {}
        self.log: set[tuple[UUID, str, str]] = set()

    async def get_prefs(self, account_id: UUID) -> Prefs:
        return self.prefs.get(account_id, Prefs())

    async def save_prefs(self, account_id: UUID, prefs: Prefs, now: datetime) -> None:
        self.prefs[account_id] = prefs

    async def recipient(self, account_id: UUID) -> str | None:
        return self.emails.get(account_id)

    async def was_sent(self, account_id: UUID, template: Template, ref: str) -> bool:
        return (account_id, template.value, ref) in self.log

    async def record(self, account_id: UUID, template: Template, ref: str, now: datetime) -> None:
        self.log.add((account_id, template.value, ref))

    async def digest_recipients(self) -> list[UUID]:
        return sorted(a for a in self.emails if self.prefs.get(a, Prefs()).email_digest)


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    async def send(self, to: str, subject: str, body: str) -> None:
        if self.fail:
            raise InfrastructureError("provider down")
        self.sent.append((to, subject, body))
