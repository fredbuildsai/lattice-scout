"""SQL store for notification preferences and the delivery log."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import text

from scout.adapters.persistence.db import Database
from scout.notifications.domain.notification import Prefs, Template


class SqlNotificationStore:
    """Implements ``NotificationStore``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_prefs(self, account_id: UUID) -> Prefs:
        """Saved preferences or the defaults."""
        async with self._db.sessionmaker() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT email_runs, email_matches, email_digest FROM notification_pref WHERE account_id = :a"
                    ),
                    {"a": account_id},
                )
            ).first()
        return Prefs(*row) if row else Prefs()

    async def save_prefs(self, account_id: UUID, prefs: Prefs, now: datetime) -> None:
        """Upsert."""
        async with self._db.sessionmaker() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO notification_pref (account_id, email_runs, email_matches, email_digest, updated_at) "
                    "VALUES (:a, :r, :m, :d, :t) ON CONFLICT (account_id) DO UPDATE SET email_runs = :r, "
                    "email_matches = :m, email_digest = :d, updated_at = :t"
                ),
                {
                    "a": account_id,
                    "r": prefs.email_runs,
                    "m": prefs.email_matches,
                    "d": prefs.email_digest,
                    "t": now,
                },
            )

    async def recipient(self, account_id: UUID) -> str | None:
        """E-mail of an active account."""
        async with self._db.sessionmaker() as s:
            return (
                await s.execute(
                    text("SELECT email FROM account WHERE id = :a AND deleted_at IS NULL"), {"a": account_id}
                )
            ).scalar()

    async def was_sent(self, account_id: UUID, template: Template, ref: str) -> bool:
        """Whether the delivery log has this mail."""
        async with self._db.sessionmaker() as s:
            return (
                await s.execute(
                    text("SELECT 1 FROM notification WHERE account_id = :a AND template = :t AND ref = :r"),
                    {"a": account_id, "t": template.value, "r": ref},
                )
            ).first() is not None

    async def record(self, account_id: UUID, template: Template, ref: str, now: datetime) -> None:
        """Log a delivery; a repeat is ignored."""
        async with self._db.sessionmaker() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO notification (id, account_id, template, ref, sent_at) VALUES (:i, :a, :t, :r, :n) "
                    "ON CONFLICT (account_id, template, ref) DO NOTHING"
                ),
                {"i": uuid4(), "a": account_id, "t": template.value, "r": ref, "n": now},
            )

    async def digest_recipients(self) -> list[UUID]:
        """Active accounts with the digest switched on."""
        async with self._db.sessionmaker() as s:
            rows = await s.execute(
                text(
                    "SELECT p.account_id FROM notification_pref p JOIN account a ON a.id = p.account_id "
                    "WHERE p.email_digest AND a.deleted_at IS NULL ORDER BY p.account_id"
                )
            )
            return [r[0] for r in rows]
