"""Notification use cases: queue a mail, deliver it, keep preferences, send the weekly digest."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.notifications.domain.notification import Prefs, Template, render
from scout.notifications.ports.repos import EmailSender, NotificationStore
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.shared import AccountId, Clock

log = structlog.get_logger()


class Notifications:
    """Queue and deliver mails. ``notify`` never raises: a lost mail must not fail the caller."""

    def __init__(
        self, store: NotificationStore, sender: EmailSender, jobs: JobQueuePort, clock: Clock, base_url: str
    ) -> None:
        self._store, self._sender, self._jobs, self._clock, self._base = store, sender, jobs, clock, base_url

    async def notify(self, account_id: UUID, template: Template, ref: str) -> None:
        """Queue ``notification.send`` (deduped per account, template and ref)."""
        try:
            await self._jobs.enqueue(
                JobSpec(
                    kind="notification.send",
                    payload={
                        "account_id": str(account_id),
                        "template": template.value,
                        "ref": ref,
                        "feature": "notification",
                    },  # fmt: skip
                    dedupe_key=f"mail:{account_id}:{template.value}:{ref}",
                    max_attempts=5,
                )
            )
        except Exception:
            log.error("notification.enqueue_failed", template=template.value)

    async def deliver(self, payload: dict[str, Any]) -> dict[str, JsonValue]:
        """Job handler: send unless the account opted out, is gone, or already got this mail.

        Raises:
            InfrastructureError: the provider failed (the worker retries).
        """
        account_id, template, ref = (
            UUID(str(payload["account_id"])),
            Template(payload["template"]),
            str(payload["ref"]),
        )
        prefs = await self._store.get_prefs(account_id)
        if not prefs.allows(template):
            return {"skipped": "opted_out"}
        to = await self._store.recipient(account_id)
        if to is None:
            return {"skipped": "no_recipient"}
        if await self._store.was_sent(account_id, template, ref):
            return {"skipped": "already_sent"}
        msg = render(template, self._base)
        await self._sender.send(to, msg.subject, msg.body)
        await self._store.record(account_id, template, ref, self._clock.now())
        return {"sent": template.value}

    async def get_prefs(self, account_id: AccountId) -> Prefs:
        """Preferences of an account."""
        return await self._store.get_prefs(account_id)

    async def update_prefs(
        self, account_id: AccountId, *, runs: bool | None, matches: bool | None, digest: bool | None
    ) -> Prefs:
        """Apply a partial update and return the result."""
        cur = await self._store.get_prefs(account_id)
        new = Prefs(
            email_runs=cur.email_runs if runs is None else runs,
            email_matches=cur.email_matches if matches is None else matches,
            email_digest=cur.email_digest if digest is None else digest,
        )
        await self._store.save_prefs(account_id, new, self._clock.now())
        return new


class MatchMailNotifier:
    """``MatchNotifier`` that queues mails. There is no decline method, by design."""

    def __init__(self, notifications: Notifications) -> None:
        self._n = notifications

    async def proposed(self, account_id: AccountId, match_id: UUID) -> None:
        """A new proposal is waiting."""
        await self._n.notify(account_id, Template.MATCH_PROPOSED, str(match_id))

    async def mutual(self, account_id: AccountId, match_id: UUID) -> None:
        """Both sides accepted."""
        await self._n.notify(account_id, Template.MATCH_MUTUAL, str(match_id))


class WeeklyDigest:
    """Job ``notification.digest``: Mondays 07:00 UTC, to opted-in accounts only. Carries no counts, so it
    cannot reveal anything about a match."""

    KIND = "notification.digest"

    def __init__(
        self, store: NotificationStore, notifications: Notifications, jobs: JobQueuePort, clock: Clock
    ) -> None:
        self._store, self._n, self._jobs, self._clock = store, notifications, jobs, clock

    async def ensure_scheduled(self) -> None:
        """Queue the next Monday 07:00 UTC (deduped per date)."""
        now = self._clock.now()
        nxt = now.replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=(7 - now.weekday()) % 7)
        if nxt <= now:
            nxt += timedelta(days=7)
        await self._jobs.enqueue(
            JobSpec(kind=self.KIND, run_at=nxt, dedupe_key=f"digest:{nxt.date()}", max_attempts=3)
        )

    async def handle(self, payload: dict[str, Any], *, last_attempt: bool = False) -> dict[str, JsonValue]:
        """Schedule the next run first, then queue one mail per opted-in account."""
        await self.ensure_scheduled()
        week = self._clock.now().date().isocalendar()
        ids = await self._store.digest_recipients()
        for account_id in ids:
            await self._n.notify(account_id, Template.WEEKLY_DIGEST, f"{week.year}-W{week.week}")
        return {"queued": len(ids)}
