"""M6 slice D: templates, preferences, delivery, digest (plan M6 task 1)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID

import pytest
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.notifications import FakeNotificationStore, FakeSender

from scout.notifications.application.service import MatchMailNotifier, Notifications, WeeklyDigest
from scout.notifications.domain.notification import Prefs, Template, render
from scout.shared import AccountId, FixedClock, InfrastructureError, SequentialIdGenerator

A = AccountId(UUID(int=1))
B = AccountId(UUID(int=2))
MONDAY = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)
BASE = "https://scout.example.edu"


class Env:
    def __init__(self, now: datetime = MONDAY) -> None:
        self.clock = FixedClock(now)
        self.jobs = InMemoryJobQueue(self.clock, SequentialIdGenerator(1))
        self.store, self.sender = FakeNotificationStore(), FakeSender()
        self.store.emails = {A: "a@example.edu", B: "b@example.edu"}
        self.n = Notifications(self.store, self.sender, self.jobs, self.clock, BASE)

    def kinds(self) -> list[str]:
        return [j.kind for j in self.jobs.jobs.values()]


# --------------------------------------------------------------------------- copy
@pytest.mark.parametrize("template", list(Template))
def test_every_mail_links_into_the_app_and_names_nobody(template: Template) -> None:
    m = render(template, BASE + "/")
    assert BASE in m.body and "//opp" not in m.body
    text = m.subject + m.body
    assert not re.search(r"\b(19|20)\d{2}\b|doi|orcid|@|decline|declined", text, re.I)


def test_preferences_default_to_digest_off() -> None:
    p = Prefs()
    assert (
        p.allows(Template.RUN_FINISHED)
        and p.allows(Template.MATCH_PROPOSED)
        and p.allows(Template.MATCH_MUTUAL)
    )
    assert not p.allows(Template.WEEKLY_DIGEST)


# --------------------------------------------------------------------------- delivery
async def test_notify_queues_one_job_per_mail_and_deliver_sends_once() -> None:
    e = Env()
    await e.n.notify(A, Template.RUN_FINISHED, "run-1")
    await e.n.notify(A, Template.RUN_FINISHED, "run-1")
    assert e.kinds() == ["notification.send"]
    payload = next(iter(e.jobs.jobs.values())).payload
    assert await e.n.deliver(payload) == {"sent": "run_finished"}
    assert await e.n.deliver(payload) == {"skipped": "already_sent"}
    assert [s[0] for s in e.sender.sent] == ["a@example.edu"]


async def test_opt_out_and_missing_recipient_skip_silently() -> None:
    e = Env()
    await e.n.update_prefs(A, runs=False, matches=None, digest=None)
    p = {"account_id": str(A), "template": "run_finished", "ref": "r"}
    assert await e.n.deliver(p) == {"skipped": "opted_out"}
    assert await e.n.deliver({**p, "account_id": str(UUID(int=99))}) == {"skipped": "no_recipient"}
    assert e.sender.sent == []


async def test_provider_failure_raises_for_retry_and_logs_nothing() -> None:
    e = Env()
    e.sender.fail = True
    p = {"account_id": str(A), "template": "match_mutual", "ref": "m"}
    with pytest.raises(InfrastructureError):
        await e.n.deliver(p)
    assert e.store.log == set()
    e.sender.fail = False
    assert await e.n.deliver(p) == {"sent": "match_mutual"}


async def test_partial_preference_update_changes_only_what_is_sent() -> None:
    e = Env()
    assert await e.n.update_prefs(A, runs=None, matches=None, digest=True) == Prefs(True, True, True)
    assert await e.n.update_prefs(A, runs=False, matches=None, digest=None) == Prefs(False, True, True)


async def test_match_notifier_queues_proposed_and_mutual_mails() -> None:
    e = Env()
    n = MatchMailNotifier(e.n)
    await n.proposed(A, UUID(int=7))
    await n.mutual(B, UUID(int=7))
    templates = sorted(j.payload["template"] for j in e.jobs.jobs.values())
    assert templates == ["match_mutual", "match_proposed"]


# --------------------------------------------------------------------------- digest
async def test_digest_goes_only_to_opted_in_accounts_and_reschedules_for_next_monday() -> None:
    e = Env()
    await e.n.update_prefs(B, runs=None, matches=None, digest=True)
    d = WeeklyDigest(e.store, e.n, e.jobs, e.clock)
    assert await d.handle({}) == {"queued": 1}
    sends = [j for j in e.jobs.jobs.values() if j.kind == "notification.send"]
    assert [j.payload["account_id"] for j in sends] == [str(B)]
    nxt = [j for j in e.jobs.jobs.values() if j.kind == "notification.digest"]
    assert len(nxt) == 1 and nxt[0].run_at.weekday() == 0 and nxt[0].run_at.hour == 7
    assert nxt[0].run_at.date().isoformat() == "2026-10-26"  # today 06:00 is before 07:00 Monday
