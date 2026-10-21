"""``MatchingService`` (reads) and ``DecideOnMatch``: the only paths that build match views."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from scout.matching.application.config import MatchConfig
from scout.matching.domain.agent_io import JointIdea
from scout.matching.domain.enums import Action, IdeasStatus, ViewerStatus
from scout.matching.domain.proposal import MatchBecameMutual, MatchExpired, MatchNotFound, MatchProposal
from scout.matching.domain.reveal import (
    AnonymousMatchView,
    JointIdeaView,
    RevealedMatchView,
    build_anonymous,
    build_revealed,
)
from scout.matching.ports.notifier import MatchNotifier
from scout.matching.ports.repos import MatchUnitOfWork
from scout.matching.ports.sources import ConsentSource
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.shared import AccountId, Clock, Conflict

MatchCard = AnonymousMatchView | RevealedMatchView
HIDE_EXPIRED_AFTER = timedelta(days=30)


class ConsentRequired(Conflict):
    """Matching needs the account's consent."""

    code = "consent_required"
    title = "Matching consent required"


@dataclass(frozen=True)
class MatchList:
    """A page of cards."""

    matching_enabled: bool
    items: list[MatchCard]
    next_cursor: str | None


def _idea_view(i: JointIdea, viewer_is_a: bool) -> JointIdeaView:
    mine, theirs = (i.uses_from_a, i.uses_from_b) if viewer_is_a else (i.uses_from_b, i.uses_from_a)
    return JointIdeaView(
        title=i.title, question=i.question, uses_from_you=mine, uses_from_them=theirs,
        first_step=i.first_step, possible_output=i.possible_output,
    )  # fmt: skip


async def card_for(
    uow: MatchUnitOfWork, p: MatchProposal, viewer: AccountId, now: datetime, field_label: str
) -> MatchCard:
    """The view ``viewer`` may see: revealed only for a mutual match, anonymous otherwise."""
    if p.status_for(viewer, now) is ViewerStatus.MUTUAL:
        contact = await uow.reveal.contact(p.id, viewer)
        if contact is not None:
            ideas = [_idea_view(i, viewer == p.a_id) for i in await uow.ideas.list(p.id)]
            return build_revealed(p, viewer, contact, ideas)
    return build_anonymous(p, viewer, now, field_label)


class MatchingService:
    """List and read matches."""

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], consents: ConsentSource, clock: Clock, cfg: MatchConfig
    ) -> None:
        self._uow, self._consents, self._clock, self._cfg = uow, consents, clock, cfg

    def _label(self) -> str:
        return self._cfg.field.replace("-", " ")

    async def list(self, viewer: AccountId, cursor: str | None = None, limit: int = 20) -> MatchList:
        """Newest first; ``declined_by_you`` and long-expired proposals are hidden."""
        now, offset = self._clock.now(), int(cursor or 0)
        consent = await self._consents.get(viewer)
        items: list[MatchCard] = []
        async with self._uow() as uow:
            rows = await uow.matches.list_for(viewer, limit + 1, offset)
            for p in rows[:limit]:
                status = p.status_for(viewer, now)
                if status is ViewerStatus.DECLINED_BY_YOU or (
                    status is ViewerStatus.EXPIRED and now - p.expires_at > HIDE_EXPIRED_AFTER
                ):
                    continue
                items.append(await card_for(uow, p, viewer, now, self._label()))
        nxt = str(offset + limit) if len(rows) > limit else None
        return MatchList(bool(consent and consent.matching), items, nxt)

    async def get(self, viewer: AccountId, match_id: UUID) -> MatchCard:
        """One card.

        Raises:
            MatchNotFound: unknown, not yours, or hidden.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            p = await uow.matches.get(match_id, viewer)
            if p is None or p.status_for(viewer, now) is ViewerStatus.DECLINED_BY_YOU:
                raise MatchNotFound()
            return await card_for(uow, p, viewer, now, self._label())


class DecideOnMatch:
    """Accept, decline or withdraw."""

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], consents: ConsentSource, jobs: JobQueuePort, notifier: MatchNotifier,
        clock: Clock, cfg: MatchConfig,
    ) -> None:  # fmt: skip
        self._uow, self._consents, self._jobs, self._notifier, self._clock, self._cfg = (
            uow,
            consents,
            jobs,
            notifier,
            clock,
            cfg,
        )

    async def execute(
        self, viewer: AccountId, match_id: UUID, action: Action, never_similar: bool = False
    ) -> MatchCard:
        """Apply the action in one transaction; enqueue the reveal after a mutual accept.

        Raises:
            ConsentRequired: the viewer has not consented to matching.
            MatchNotFound, MatchExpired, MatchClosed, NothingToWithdraw: see ``MatchProposal.decide``.
        """
        consent = await self._consents.get(viewer)
        if consent is None or not consent.matching:
            raise ConsentRequired()
        now = self._clock.now()
        mutual = False
        expired: MatchExpired | None = None
        async with self._uow() as uow:
            p = await uow.matches.get_for_update(match_id, viewer)
            if p is None:
                raise MatchNotFound()
            try:
                events = p.decide(viewer, action, now, never_similar=never_similar)
            except MatchExpired as exc:
                expired = exc
                events = []
            if expired is None:
                mutual = any(isinstance(e, MatchBecameMutual) for e in events)
                if mutual:
                    p.ideas_status = IdeasStatus.PENDING
                if never_similar and p.never_similar_by is not None:
                    other = p.b_id if viewer == p.a_id else p.a_id
                    if other is not None:  # a detached counterpart (erased account) has nothing to block
                        await uow.blocks.add(viewer, other, p.id, now)
            await uow.matches.save(p)
            await uow.commit()
            card = None if expired else await card_for(uow, p, viewer, now, self._cfg.field.replace("-", " "))
        if expired is not None or card is None:
            raise expired or MatchExpired()
        if mutual:
            await self._jobs.enqueue(
                JobSpec(
                    kind="matching.reveal",
                    payload={"match_id": str(match_id)},
                    dedupe_key=f"reveal:{match_id}",
                    max_attempts=3,
                )
            )
            for who in (w for w in (p.a_id, p.b_id) if w is not None):
                try:
                    await self._notifier.mutual(who, match_id)
                except Exception:  # noqa: S110 - best effort
                    pass
        return card
