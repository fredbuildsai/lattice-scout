"""``ComputeMatches``: candidates, complementarity score, filters, weekly cap, explanation, proposals."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from scout.matching.application.config import MatchConfig
from scout.matching.domain.agent_io import (
    MatchExplainerInput,
    Perspective,
    ResearcherSummary,
    ScoreBreakdown,
    SummaryMethod,
    SummaryTopic,
)
from scout.matching.domain.enums import CareerBand, Region
from scout.matching.domain.guards import leaks_identity
from scout.matching.domain.ids import iso_week, match_id
from scout.matching.domain.profile import MatchProfile
from scout.matching.domain.proposal import BringItem, MatchProposal, PublicFacet, StoredRationale
from scout.matching.domain.scoring import ScoredPair, score_pair, select_pairs
from scout.matching.domain.vocabulary import Vocabulary
from scout.matching.ports.agents import MatchAgentsPort
from scout.matching.ports.notifier import MatchNotifier
from scout.matching.ports.repos import MatchUnitOfWork
from scout.ports.llm import CallContext
from scout.shared import AccountId, Clock

Pair = tuple[AccountId, AccountId]


def template_rationale(
    a: MatchProfile, b: MatchProfile, scored: ScoredPair, vocab: Vocabulary
) -> StoredRationale:
    """Controlled-vocabulary explanation: method labels and topic names only, nothing from the people's texts."""

    def labels(ids: Sequence[str]) -> list[BringItem]:
        return [BringItem(text=f"Experience with {vocab.label(i) or i}") for i in ids[:3]]

    names = {t.id: t.name for t in (*a.topics, *b.topics)}
    shared = [names.get(i, i) for i in scored.basis.shared_topics[:2]]
    why = f"You work on {' and '.join(shared)}, and each of you brings methods the other lacks." if shared else (
        "You work on related topics, and each of you brings methods the other lacks."
    )  # fmt: skip
    return StoredRationale(
        source="template",
        you_bring=labels(scored.basis.a_only),
        they_bring=labels(scored.basis.b_only),
        why=why[:300],
    )


def _summary(p: MatchProfile, only_methods: Sequence[str], vocab: Vocabulary) -> ResearcherSummary:
    return ResearcherSummary(
        topics=[SummaryTopic(id=t.id, name=t.name, weight=t.weight) for t in p.topics[:10]],
        methods=[SummaryMethod(id=i, label=vocab.label(i) or i) for i in only_methods[:10]],
        works=[],
    )


def valid_perspective(p: Perspective, titles: Sequence[str]) -> StoredRationale | None:
    """Keep only items without identity leaks; None when nothing usable is left."""

    def keep(items: Sequence[BringItem]) -> list[BringItem]:
        return [i for i in items if not leaks_identity(i.text, titles)]

    you, they = keep(p.you_bring), keep(p.they_bring)
    if not you or not they or leaks_identity(p.why, titles):
        return None
    # source_refs are dropped: the explainer gets no works, so any ref it returns is invented (design section 5)
    return StoredRationale(
        source="llm", you_bring=[BringItem(text=i.text) for i in you],
        they_bring=[BringItem(text=i.text) for i in they], why=p.why,
    )  # fmt: skip


class ComputeMatches:
    """Job ``matching.compute`` (payload ``account_id`` optional)."""

    KIND = "matching.compute"

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], agents: MatchAgentsPort, vocabulary: Vocabulary,
        notifier: MatchNotifier, clock: Clock, cfg: MatchConfig,
    ) -> None:  # fmt: skip
        self._uow, self._agents, self._vocab, self._notifier, self._clock, self._cfg = (
            uow,
            agents,
            vocabulary,
            notifier,
            clock,
            cfg,
        )

    async def handle(self, payload: dict[str, Any], *, last_attempt: bool = False) -> dict[str, Any]:
        """Run for one account or for everyone."""
        raw = payload.get("account_id")
        return await self.execute(AccountId(UUID(str(raw))) if raw else None)

    async def execute(self, account: AccountId | None = None) -> dict[str, Any]:
        """Create proposals for the best new pairs; idempotent within an ISO week."""
        now, cfg = self._clock.now(), self._cfg
        async with self._uow() as uow:
            pairs = await uow.candidates.candidates(
                field=cfg.field, account=account, exclude_coauthors=cfg.exclude_coauthors,
                exclude_same_group=cfg.exclude_same_group, cooldown_since=now - timedelta(days=cfg.cooldown_days),
                limit=cfg.candidate_limit, per_person=cfg.candidates_per_person,
            )  # fmt: skip
            ids = sorted({x for p in pairs for x in p})
            profiles = await uow.profiles.get_many(ids)
            counts = await uow.matches.open_counts(ids, now - timedelta(days=7))
        scored: dict[Pair, ScoredPair] = {}
        for a, b in pairs:
            pa, pb = profiles.get(a), profiles.get(b)
            if pa is None or pb is None:
                continue
            s = score_pair(
                a_topics={t.id: t.weight for t in pa.topics}, b_topics={t.id: t.weight for t in pb.topics},
                a_methods=pa.methods, b_methods=pb.methods, a_activity=pa.activity, b_activity=pb.activity,
                alpha=cfg.alpha, beta=cfg.beta, min_score=cfg.min_score,
            )  # fmt: skip
            if s is not None:
                scored[(a, b)] = s
        chosen = select_pairs(
            [(a, b, s.score) for (a, b), s in scored.items()], open_counts=counts, cap=cfg.weekly_cap
        )
        proposed, explains = 0, 0
        for a, b, _ in chosen:
            s, pa, pb = scored[(a, b)], profiles[a], profiles[b]
            rationale, used_llm = await self._explain(pa, pb, s, explains < cfg.max_explains_per_run)
            explains += int(used_llm)
            p = MatchProposal.propose(
                id=match_id(a, b, iso_week(now)), a_id=a, b_id=b, field=cfg.field, score=s.score, score_basis=s.basis,
                rationale=rationale, a_public=PublicFacet(region=Region(pa.region), career_band=CareerBand(pa.career_band)),
                b_public=PublicFacet(region=Region(pb.region), career_band=CareerBand(pb.career_band)), now=now,
                expiry_days=cfg.expiry_days,
            )  # fmt: skip
            async with self._uow() as uow:
                created = await uow.matches.add(p)
                await uow.commit()
            if created:
                proposed += 1
                for who in (a, b):
                    try:
                        await self._notifier.proposed(who, p.id)
                    except Exception:  # noqa: S110 - best effort, never fails the job
                        pass
        return {"candidates": len(pairs), "scored": len(scored), "proposed": proposed}

    async def _explain(
        self, a: MatchProfile, b: MatchProfile, s: ScoredPair, allow_llm: bool
    ) -> tuple[StoredRationale, bool]:
        template = template_rationale(a, b, s, self._vocab)
        if not allow_llm:
            return template, False
        names = {t.id: t.name for t in (*a.topics, *b.topics)}
        data = MatchExplainerInput(
            a=_summary(a, s.basis.a_only, self._vocab), b=_summary(b, s.basis.b_only, self._vocab),
            score_breakdown=ScoreBreakdown(
                shared_topics=[names.get(i, i) for i in s.basis.shared_topics],
                a_only_methods=[self._vocab.label(i) or i for i in s.basis.a_only],
                b_only_methods=[self._vocab.label(i) or i for i in s.basis.b_only], score=s.score,
            ),
        )  # fmt: skip
        try:
            out = await self._agents.explain(
                data,
                CallContext(
                    feature="matching.explain", account_id=a.account_id, session_id=None, run_id=None
                ),
            )
        except Exception:
            return template, False
        checked = valid_perspective(out.output.for_a, [])
        if checked is None:
            return template, True
        # Side B reads text written for B (F-38). If it fails the guard, B gets the neutral template sentence.
        why_b = out.output.for_b.why
        if leaks_identity(why_b, []):
            why_b = template.why
        return checked.model_copy(update={"prompt_version": out.prompt_version, "why_b": why_b}), True
