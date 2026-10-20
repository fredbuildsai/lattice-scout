"""Profile use cases: FindAuthorCandidates, ClaimProfile, GetProfile, ToggleWork."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from pydantic import JsonValue

from scout.ports.audit import AuditEntry
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.literature import LiteraturePort
from scout.profile.application.mapping import to_snapshot
from scout.profile.domain.grouping import AuthorSnapshot, CandidateGroup, group_candidates
from scout.profile.domain.researcher import ImportStatus, Researcher
from scout.profile.domain.topics import compute_topics
from scout.profile.domain.work import ClaimedWork
from scout.profile.ports.repos import ProfileUnitOfWork
from scout.shared import AccountId, Clock, IdGenerator, InfrastructureError, NotFound, ValidationFailed

_AUTHOR_ID = re.compile(r"^A\d+$")


class ProfileNotFound(NotFound):
    """The account has not claimed a profile yet."""

    code = "profile_not_found"
    title = "Profile not found"


class UnknownAuthor(ValidationFailed):
    """An OpenAlex author id does not exist."""

    code = "unknown_author"
    title = "Unknown OpenAlex author"


@dataclass(frozen=True)
class ProfileConfig:
    """Limits of the profile use cases."""

    max_groups: int = 10
    enrich_groups: int = 3
    max_claim_ids: int = 10
    works_limit: int = 500
    max_topics: int = 25


@dataclass(frozen=True)
class ProfileView:
    """A researcher with all their claimed works."""

    researcher: Researcher
    works: list[ClaimedWork]


def short(author_id: str) -> str:
    """``https://openalex.org/A1`` -> ``A1``."""
    return author_id.strip().rsplit("/", 1)[-1]


class ProfileService:
    """Application service of the profile context."""

    def __init__(
        self,
        uow: Callable[[], ProfileUnitOfWork],
        literature: LiteraturePort,
        jobs: JobQueuePort,
        clock: Clock,
        ids: IdGenerator,
        cfg: ProfileConfig = ProfileConfig(),  # noqa: B008
    ) -> None:
        self._uow, self._lit, self._jobs, self._clock, self._ids, self._cfg = (
            uow,
            literature,
            jobs,
            clock,
            ids,
            cfg,
        )

    # --- discovery of candidates -------------------------------------------------------------------------
    async def find_candidates(self, name: str, affiliation: str | None = None) -> list[CandidateGroup]:
        """Search OpenAlex and group split records. No database access; reveals nothing about other users."""
        name = name.strip()
        if not 2 <= len(name) <= 200:
            raise ValidationFailed("Name must have 2 to 200 characters")
        hits = await self._lit.search_authors(name, affiliation or None)
        snaps: dict[str, AuthorSnapshot] = {h.id: to_snapshot(h) for h in hits}
        orcids = list(dict.fromkeys(h.orcid for h in hits if h.orcid))[: self._cfg.enrich_groups]
        for orcid in orcids:
            for rec in await self._lit.authors_by_orcid(orcid):
                snaps.setdefault(rec.id, to_snapshot(rec))
        groups = group_candidates(list(snaps.values()), affiliation)[: self._cfg.max_groups]
        enriched: list[CandidateGroup] = []
        for i, g in enumerate(groups):
            if i < self._cfg.enrich_groups and not g.primary.topics:
                try:
                    full = to_snapshot(await self._lit.get_author(g.primary.id))
                except NotFound:
                    enriched.append(g)  # enrichment is best effort
                    continue
                g = CandidateGroup(
                    primary=g.primary,
                    records=tuple(
                        r.model_copy(update={"snapshot": r.snapshot.model_copy(update={"topics": full.topics})})
                        if r.snapshot.id == g.primary.id else r
                        for r in g.records
                    ),
                    possible=g.possible,
                )  # fmt: skip
            enriched.append(g)
        return enriched

    # --- claim --------------------------------------------------------------------------------------------
    async def _audit(
        self, uow: ProfileUnitOfWork, actor: AccountId, action: str, target: str, meta: dict[str, JsonValue]
    ) -> None:
        await uow.audit.add(
            AuditEntry(
                id=self._ids.new(),
                actor_id=actor,
                action=action,
                target=target,
                at=self._clock.now(),
                meta=meta,
            )
        )

    async def claim(
        self,
        account_id: AccountId,
        author_ids: list[str],
        primary_author_id: str | None,
        session_id: str | None,
    ) -> Researcher:
        """Claim one or more OpenAlex records (idempotent) and enqueue ``profile.import``."""
        ids = list(dict.fromkeys(short(a) for a in author_ids))
        if not 1 <= len(ids) <= self._cfg.max_claim_ids or not all(_AUTHOR_ID.match(a) for a in ids):
            raise ValidationFailed("Provide 1 to 10 OpenAlex author ids such as A5059588273")
        primary = short(primary_author_id) if primary_author_id else ids[0]
        if primary not in ids:
            raise ValidationFailed("primary_author_id must be one of author_ids")
        ids.remove(primary)
        ids.insert(0, primary)
        snaps: list[AuthorSnapshot] = []
        for aid in ids:  # outside any transaction
            try:
                snaps.append(to_snapshot(await self._lit.get_author(aid)))
            except NotFound as exc:
                raise UnknownAuthor(
                    f"OpenAlex author {aid} does not exist", extra={"author_id": aid}
                ) from exc

        now = self._clock.now()
        enqueue = False
        async with self._uow() as uow:
            current = await uow.researchers.get_by_account(account_id)
            old_ids: set[str] = set(current.author_ids) if current else set()
            if (
                current is not None
                and current.same_claim(ids, primary)
                and current.import_status != ImportStatus.FAILED
            ):
                researcher, changed = current, False
                enqueue = current.import_status == ImportStatus.QUEUED
            elif current is not None:
                current.reclaim(snaps, primary, now)
                researcher, changed, enqueue = current, True, True
            else:
                researcher = Researcher.claim(self._ids.new(), account_id, snaps, primary, now)
                changed, enqueue = True, True
            if changed:
                affected = await uow.researchers.find_by_author_ids(set(ids) | old_ids)
                everyone = {r.id: r for r in affected if r.id != researcher.id}
                conflicting: list[UUID] = []
                for other in everyone.values():
                    shares = set(other.author_ids) & set(researcher.author_ids)
                    if shares:
                        conflicting.append(other.id)
                researcher.recompute_verification(contested=bool(conflicting))
                for other in everyone.values():
                    rivals = [
                        x for x in [researcher, *everyone.values()]
                        if x.id != other.id and set(x.author_ids) & set(other.author_ids)
                    ]  # fmt: skip
                    other.recompute_verification(contested=bool(rivals))
                    await uow.researchers.save(other)
                if current is None:
                    await uow.researchers.add(researcher)
                else:
                    await uow.researchers.save(researcher)
                researcher.pull_events()
                await self._audit(
                    uow, account_id, "profile.claimed", str(researcher.id),
                    {"author_ids": list(researcher.author_ids), "generation": researcher.import_generation},
                )  # fmt: skip
                if conflicting:
                    await self._audit(
                        uow, account_id, "profile.claim_conflict", str(researcher.id),
                        {"author_ids": list(researcher.author_ids), "other_researcher_ids": [str(c) for c in conflicting]},
                    )  # fmt: skip
                await uow.commit()
        if enqueue:
            try:
                await self._enqueue_import(account_id, session_id, researcher)
            except Exception as exc:  # the client re-POSTs; claim is idempotent
                raise InfrastructureError("Could not schedule the import, please retry") from exc
        return researcher

    async def _enqueue_import(
        self, account_id: AccountId, session_id: str | None, researcher: Researcher
    ) -> None:
        await self._jobs.enqueue(
            JobSpec(
                kind="profile.import",
                payload={
                    "account_id": str(account_id),
                    "session_id": session_id,
                    "feature": "profile.import",
                    "researcher_id": str(researcher.id),
                    "generation": researcher.import_generation,
                },
                dedupe_key=f"{researcher.id}:{researcher.import_generation}",
                max_attempts=5,
            )  # fmt: skip
        )

    # --- reads / toggles ----------------------------------------------------------------------------------
    async def get_profile(self, account_id: AccountId) -> ProfileView:
        """The caller's profile with works, or ``ProfileNotFound``."""
        async with self._uow() as uow:
            r = await uow.researchers.get_by_account(account_id)
            if r is None:
                raise ProfileNotFound()
            works = await uow.works.list_for(r.id)
        works.sort(key=lambda w: (-(w.year or 0), -w.cited_by, w.openalex_work_id))
        return ProfileView(r, works)

    async def summary(self, account_id: AccountId) -> Researcher | None:
        """The researcher or None."""
        async with self._uow() as uow:
            return await uow.researchers.get_by_account(account_id)

    async def toggle_work(self, account_id: AccountId, work_id: UUID, included: bool) -> ClaimedWork:
        """Include/exclude a work; topics are recomputed. Another user's work is a 404."""
        async with self._uow() as uow:
            r = await uow.researchers.get_by_account(account_id)
            if r is None:
                raise ProfileNotFound()
            work = await uow.works.get(work_id, r.id)
            if work is None:
                raise NotFound("Work not found")
            work.toggle(included)
            await uow.works.save(work)
            all_works = await uow.works.list_for(r.id)
            r.topics = compute_topics(all_works, self._cfg.max_topics)
            await uow.researchers.save(r)
            await uow.commit()
        return work
