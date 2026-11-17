"""``ExternalSuggestions``: find researchers who are not on Lattice Scout in OpenAlex and keep them as suggestions.

Ported from the sister project Faraday (``discovery-external``). No LLM; published OpenAlex data only (ADR 016).
External people are never contacted and never enter match proposals or the double opt-in (DECISIONS 132).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import TypeVar
from uuid import UUID

import structlog

from scout.matching.application.config import ExternalConfig
from scout.matching.domain.external import (
    MAX_TOPICS,
    AuthorRef,
    AuthorTopic,
    Bridge,
    ExternalAuthor,
    ExternalLayer,
    ExternalSuggestion,
    SuggestionState,
    WorkAuthors,
    collect_candidates,
    direct_coauthors,
    lookup_pool,
    rank_external,
    recent_since,
    top_coauthors,
    topical_pages,
)
from scout.matching.domain.profile import activity, compute_topics
from scout.matching.ports.repos import MatchUnitOfWork
from scout.matching.ports.sources import MemberDirectory, ProfileSource
from scout.ports.literature import AuthorRecord, LiteraturePort, WorkFilter, WorkRecord
from scout.shared import AccountId, Clock, Conflict, IdGenerator, NotFound, RateLimited

log = structlog.get_logger()
T = TypeVar("T")

VISIBLE = (SuggestionState.ACTIVE, SuggestionState.SAVED)


class ProfileRequired(Conflict):
    """External suggestions need a claimed profile (your OpenAlex ids and topics)."""

    code = "profile_required"
    title = "Claim your profile first"


class ExternalRefreshLimit(RateLimited):
    """Too many searches for external researchers today."""

    code = "external_refresh_limit"
    title = "Daily search limit reached"


class SuggestionNotFound(NotFound):
    """Unknown suggestion, or not yours."""

    code = "suggestion_not_found"
    title = "Suggestion not found"


def work_authors(w: WorkRecord) -> WorkAuthors:
    """``WorkRecord`` -> ``WorkAuthors``; names are paired with ids only when the lists line up."""
    names = w.author_names if len(w.author_names) == len(w.author_ids) else ("",) * len(w.author_ids)
    last = AuthorRef(w.last_author_id, w.last_author_name or "") if w.last_author_id else None
    return WorkAuthors(tuple(AuthorRef(i, n) for i, n in zip(w.author_ids, names, strict=True)), last)


def external_author(a: AuthorRecord) -> ExternalAuthor:
    """``AuthorRecord`` -> ``ExternalAuthor`` (public facts only)."""
    return ExternalAuthor(
        id=a.id, name=a.display_name, institution=a.institutions[0] if a.institutions else None,
        topics=tuple(AuthorTopic(t.id, t.name or t.id, t.count or 0) for t in a.topics),
        works_by_year={c.year: c.works_count for c in a.counts_by_year}, h_index=a.h_index or 0,
        citations=a.cited_by_count, works_count=a.works_count,
    )  # fmt: skip


class ExternalSuggestions:
    """List, refresh and triage suggestions of researchers who are not on the platform."""

    def __init__(
        self,
        uow: Callable[[], MatchUnitOfWork],
        profiles: ProfileSource,
        members: MemberDirectory,
        literature: LiteraturePort,
        clock: Clock,
        ids: IdGenerator,
        cfg: ExternalConfig = ExternalConfig(),  # noqa: B008
    ) -> None:
        self._uow, self._profiles, self._members, self._lit = uow, profiles, members, literature
        self._clock, self._ids, self._cfg = clock, ids, cfg

    async def visible(
        self, account: AccountId, layer: ExternalLayer | None = None
    ) -> list[ExternalSuggestion]:
        """Active and saved suggestions, ``in_network`` first, then best score. Dismissed ones are hidden."""
        async with self._uow() as uow:
            return await uow.external.list_for(account, VISIBLE, layer)

    async def set_state(
        self, account: AccountId, suggestion_id: UUID, state: SuggestionState
    ) -> ExternalSuggestion:
        """Save, dismiss or restore one suggestion.

        Raises:
            SuggestionNotFound: unknown or someone else's.
        """
        async with self._uow() as uow:
            s = await uow.external.get(suggestion_id, account)
            if s is None:
                raise SuggestionNotFound()
            s.set_state(state, self._clock.now())
            await uow.external.save(s)
            await uow.commit()
        return s

    async def refresh(self, account: AccountId, scopes: Sequence[ExternalLayer]) -> list[ExternalSuggestion]:
        """Search OpenAlex for new suggestions in ``scopes``, store them, return the visible list.

        Raises:
            ExternalRefreshLimit: more than ``refresh_per_day`` refreshes in 24 hours.
            ProfileRequired: no claimed profile.
        """
        now = self._clock.now()
        data = await self._profiles.matching_input(account)
        if data is None or not data.author_ids:
            raise ProfileRequired()
        async with self._uow() as uow:
            limit = self._cfg.refresh_per_day
            if limit and await uow.external.refreshes_since(account, now - timedelta(days=1)) >= limit:
                raise ExternalRefreshLimit()
            await uow.external.record_refresh(account, now)
            stored = await uow.profiles.get(account)
            seen = await uow.external.seen_ids(account)
            await uow.commit()
        if stored is not None and stored.topics:
            my_topics = {t.id: t.weight for t in stored.topics}
            names = {t.id: t.name for t in stored.topics}
            my_activity = stored.activity
        else:  # no matching profile (no consent yet): the same pure computation from the included works
            computed = compute_topics(data.works, now)
            my_topics, names = {t.id: t.weight for t in computed}, {t.id: t.name for t in computed}
            my_activity = activity(data.works, now)
        wanted = set(scopes)
        if not my_topics or not wanted:
            return await self.visible(account)

        sem = asyncio.Semaphore(max(1, self._cfg.concurrency))

        async def safe(call: Awaitable[list[T]], what: str) -> list[T]:
            async with sem:
                try:
                    return await call
                except Exception as exc:  # one failed OpenAlex call must not sink the refresh
                    log.warning("external_openalex_failed", call=what, error=type(exc).__name__)
                    return []

        own = set(data.author_ids)
        mine = [
            work_authors(w)
            for batch in await asyncio.gather(
                *(
                    safe(self._lit.list_author_works(a, self._cfg.my_works_limit), "my_works")
                    for a in data.author_ids
                )
            )
            for w in batch
        ]
        exclude = own | direct_coauthors(mine) | await self._members.claimed_author_ids() | seen
        since = recent_since(now)

        bridges: list[Bridge] = []
        if ExternalLayer.IN_NETWORK in wanted:
            bridges = top_coauthors(mine, own, self._cfg.bridge_coauthors)
        bridge_works = await asyncio.gather(
            *(
                safe(
                    self._lit.works_by_filter(WorkFilter(author_id=b.id, from_publication_date=since)),
                    "bridge",
                )
                for b in bridges
            )
        )
        topical: list[WorkAuthors] = []
        if ExternalLayer.IN_FIELD in wanted:
            top = [k for k, _ in sorted(my_topics.items(), key=lambda kv: (-kv[1], kv[0]))][:MAX_TOPICS]
            pages = await asyncio.gather(
                *(
                    safe(
                        self._lit.works_by_filter(
                            WorkFilter(
                                topic_ids=tuple(top),
                                from_publication_date=since,
                                sort="cited_by_count:desc",
                                page=p,
                            )
                        ),
                        "topical",
                    )
                    for p in topical_pages(len(seen))
                )
            )
            topical = [work_authors(w) for page in pages for w in page]

        raws = collect_candidates(
            [(b, [work_authors(w) for w in works]) for b, works in zip(bridges, bridge_works, strict=True)],
            topical, exclude, self._cfg.pi_only,
        )  # fmt: skip
        pool = lookup_pool(raws, self._cfg.max_lookups, self._cfg.network_share)
        if pool:
            records = await safe(self._lit.get_authors([c.id for c in pool]), "authors")
            authors = {a.id: external_author(a) for a in records}
            picks = rank_external(
                my_topics, my_activity, pool, authors, current_year=now.year, size=self._cfg.batch_size,
                alpha=self._cfg.alpha, min_topic=self._cfg.min_topic, min_score=self._cfg.min_score,
                my_topic_names=names,
            )  # fmt: skip
            if picks:
                async with self._uow() as uow:
                    await uow.external.add_many(
                        [
                            ExternalSuggestion.from_scored(p, id=self._ids.new(), account_id=account, now=now)
                            for p in picks
                        ]
                    )
                    await uow.commit()
        log.info("external_refresh", candidates=len(raws), looked_up=len(pool))
        return await self.visible(account)
