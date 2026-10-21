"""``RevealMatch`` (job ``matching.reveal``) generates joint ideas for a mutual match, and the nightly sweep."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID

from scout.matching.application.config import MatchConfig
from scout.matching.domain.agent_io import (
    JointIdea,
    JointIdeasInput,
    ResearcherSummary,
    SignalItem,
    SummaryMethod,
    SummaryTopic,
    SummaryWork,
)
from scout.matching.domain.enums import IdeasStatus, MatchState, Side
from scout.matching.domain.guards import copies_signal
from scout.matching.domain.profile import MatchProfile
from scout.matching.domain.proposal import MatchProposal
from scout.matching.domain.vocabulary import Vocabulary
from scout.matching.ports.agents import MatchAgentsPort
from scout.matching.ports.repos import MatchUnitOfWork
from scout.matching.ports.sources import ConsentSource, ProfileSource, VaultSignalSource
from scout.ports.budget import GlobalBudgetPort
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.llm import CallContext, LLMOutputInvalid
from scout.shared import AccountId, Clock

SIGNAL_SECTIONS = ("methods", "aims")


def check_ideas(
    ideas: list[JointIdea], refs_a: set[str], refs_b: set[str], signals: list[str]
) -> list[JointIdea]:
    """Drop ideas without a valid ref on both sides, with unknown refs, or copying a signal."""
    kept = []
    for i in ideas:
        ra = {r for b in i.uses_from_a for r in b.source_refs}
        rb = {r for b in i.uses_from_b for r in b.source_refs}
        if not (ra & refs_a and rb & refs_b) or not ra <= refs_a or not rb <= refs_b:
            continue
        text = " ".join(
            [
                i.title,
                i.question,
                i.first_step,
                i.possible_output,
                *(b.text for b in (*i.uses_from_a, *i.uses_from_b)),
            ]
        )
        if copies_signal(text, signals):
            continue
        kept.append(i)
    return kept


def _topics(pr: MatchProfile | None) -> list[SummaryTopic]:
    return [SummaryTopic(id=t.id, name=t.name, weight=t.weight) for t in (pr.topics if pr else ())]


class RevealMatch:
    """Generate ideas once, only after the match is mutual."""

    KIND = "matching.reveal"

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], profiles: ProfileSource, consents: ConsentSource, signals: VaultSignalSource,
        agents: MatchAgentsPort, vocabulary: Vocabulary, clock: Clock,
    ) -> None:  # fmt: skip
        self._uow, self._profiles, self._consents, self._signals = uow, profiles, consents, signals
        self._agents, self._vocab, self._clock = agents, vocabulary, clock

    async def _side(
        self, account: AccountId, p_methods: dict[str, float], topics: list[SummaryTopic]
    ) -> tuple[ResearcherSummary, set[str], list[SignalItem]]:
        data = await self._profiles.matching_input(account)
        works = sorted(data.works if data else (), key=lambda w: (-(w.year or 0), w.ref))[:8]
        consent = await self._consents.get(account)
        sig = (
            await self._signals.signals(account, SIGNAL_SECTIONS, 10)
            if consent and consent.vault_signals and self._agents.confidential_allowed()
            else []
        )
        summary = ResearcherSummary(
            topics=topics[:10], methods=[
                SummaryMethod(id=k, label=self._vocab.label(k) or k)
                for k, _ in sorted(p_methods.items(), key=lambda kv: (-kv[1], kv[0]))[:10]  # stable, whatever the store returns
            ],
            works=[SummaryWork(ref=w.ref, title=w.title[:200]) for w in works],
        )  # fmt: skip
        return summary, {w.ref for w in works} | {s.ref for s in sig}, sig

    async def handle(self, payload: dict[str, Any], *, last_attempt: bool = False) -> dict[str, Any]:
        """Run for ``payload['match_id']``."""
        match_id = UUID(str(payload["match_id"]))
        now = self._clock.now()
        async with self._uow() as uow:
            p = await uow.matches.get_any(match_id)
            if p is None or p.state is not MatchState.MUTUAL:
                return {"status": "skipped"}
            if p.ideas_status is IdeasStatus.READY:
                return {"status": "ready"}
            if p.a_id is None or p.b_id is None:  # mutual proposals are deleted, never detached
                return {"status": "skipped"}
            prof = await uow.profiles.get_many([p.a_id, p.b_id])
        pa, pb = prof.get(p.a_id), prof.get(p.b_id)
        topics = _topics
        names = {t.id: t.name for pr in (pa, pb) if pr for t in pr.topics}
        sa, refs_a, sig_a = await self._side(p.a_id, dict(pa.methods) if pa else {}, topics(pa))
        sb, refs_b, sig_b = await self._side(p.b_id, dict(pb.methods) if pb else {}, topics(pb))
        try:
            out = await self._agents.joint_ideas(
                JointIdeasInput(
                    a=sa, b=sb, a_signals=sig_a, b_signals=sig_b,
                    shared_topics=[names.get(i, i) for i in p.score_basis.shared_topics],
                    a_only_methods=[self._vocab.label(i) or i for i in p.score_basis.a_only],
                    b_only_methods=[self._vocab.label(i) or i for i in p.score_basis.b_only],
                ),
                CallContext(feature="matching.ideas", account_id=p.a_id, session_id=None, run_id=None),
            )  # fmt: skip
            ideas = check_ideas(out.output.ideas, refs_a, refs_b, [s.text for s in (*sig_a, *sig_b)])
            if not ideas:
                raise LLMOutputInvalid("no_valid_ideas")
        except Exception:
            if not last_attempt:
                raise
            await self._mark(match_id, IdeasStatus.FAILED, [], "", "", now)
            return {"status": "failed"}
        await self._mark(match_id, IdeasStatus.READY, ideas, out.prompt_version, out.model, now)
        return {"status": "ready", "ideas": len(ideas)}

    async def _mark(
        self, match_id: UUID, status: IdeasStatus, ideas: list[JointIdea], version: str, model: str, now: Any
    ) -> None:
        async with self._uow() as uow:
            p = await uow.matches.get_any(match_id, lock=True)
            if p is None or p.state is not MatchState.MUTUAL:
                return
            if ideas:
                await uow.ideas.add_many(match_id, ideas, version, model, now)
            p.ideas_status = status
            await uow.matches.save(p)
            await uow.commit()


DETACHED_KEEP_DAYS = 30  # detached proposals are invisible to the counterpart long before this


class MatchingNightly:
    """Job ``matching.nightly``: expire, close for consent, retry reveals, refresh profiles, compute matches."""

    KIND = "matching.nightly"

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], jobs: JobQueuePort, profile_job: Any, compute: Any, clock: Clock, cfg: MatchConfig,
        budget: GlobalBudgetPort | None = None,
    ) -> None:  # fmt: skip
        self._uow, self._jobs, self._profile, self._compute, self._clock, self._cfg = (
            uow,
            jobs,
            profile_job,
            compute,
            clock,
            cfg,
        )
        self._budget = budget

    async def ensure_scheduled(self) -> None:
        """Queue the next run at the configured UTC hour (deduped per day)."""
        now = self._clock.now()
        nxt = now.replace(hour=self._cfg.nightly_utc_hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await self._jobs.enqueue(
            JobSpec(kind=self.KIND, payload={}, run_at=nxt, dedupe_key=f"slot:{nxt.date()}", max_attempts=3)
        )

    async def handle(self, payload: dict[str, Any], *, last_attempt: bool = False) -> dict[str, Any]:
        """One pass; the next one is scheduled first so a failure never ends the chain."""
        await self.ensure_scheduled()
        now = self._clock.now()
        expired = closed = retried = 0
        async with self._uow() as uow:
            for mid in await uow.matches.due_for_expiry(now, 500):
                p = await uow.matches.get_any(mid, lock=True)
                if p is not None and p.expire(now):
                    await uow.matches.save(p)
                    expired += 1
            for mid in await uow.matches.pending_outside_pool(500):
                p = await uow.matches.get_any(mid, lock=True)
                if p is not None:
                    await self._close_departed(uow, p, now)
                    closed += 1
            purged = await uow.matches.purge_detached(now - timedelta(days=DETACHED_KEEP_DAYS))
            stale = await uow.matches.ideas_pending_before(
                now - timedelta(minutes=self._cfg.reveal_retry_minutes), 100
            )
            await uow.commit()
            accounts = await uow.profiles.accounts()
        for mid in stale:
            await self._jobs.enqueue(
                JobSpec(
                    kind="matching.reveal",
                    payload={"match_id": str(mid)},
                    dedupe_key=f"reveal:{mid}",
                    max_attempts=3,
                )
            )
            retried += 1
        if self._budget is not None and (await self._budget.status()).paused:
            return {
                "expired": expired,
                "closed": closed,
                "reveal_retried": retried,
                "detached_purged": purged,
                "paused": True,
            }
        for acc in accounts:
            await self._profile.execute(acc)
        result = await self._compute.execute(None)
        return {
            "expired": expired,
            "closed": closed,
            "reveal_retried": retried,
            "detached_purged": purged,
            **result,
        }

    async def _close_departed(self, uow: MatchUnitOfWork, p: MatchProposal, now: Any) -> None:
        if p.a_id is None or p.b_id is None:
            return
        pool = await uow.profiles.get_many([p.a_id, p.b_id])
        for side, acc in ((Side.A, p.a_id), (Side.B, p.b_id)):
            pr = pool.get(acc)
            if pr is None or not pr.eligible:
                p.close_for_consent(side, now)
                break
        await uow.matches.save(p)
