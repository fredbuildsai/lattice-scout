"""``ComputeResearcherProfile``: topics, methods and facets of one researcher; the model runs only when inputs change."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from scout.matching.application.config import MatchConfig
from scout.matching.domain.agent_io import MethodExtractorInput, MethodSource, VocabularyEntry
from scout.matching.domain.profile import (
    MatchProfile,
    WorkIn,
    activity,
    career_band,
    compute_topics,
    is_eligible,
    method_works,
    methods_hash,
    topics_hash,
    weigh_methods,
)
from scout.matching.domain.proposal import PENDING
from scout.matching.domain.regions import region_of
from scout.matching.domain.vocabulary import Vocabulary
from scout.matching.ports.agents import MatchAgentsPort
from scout.matching.ports.repos import MatchUnitOfWork
from scout.matching.ports.sources import ConsentSource, ProfileSource, VaultSignalSource
from scout.ports.jobs import JobQueuePort, JobSpec
from scout.ports.llm import CallContext
from scout.shared import AccountId, Clock

SIGNAL_SECTIONS = ("methods",)


class ComputeResearcherProfile:
    """Job ``matching.profile`` (payload ``account_id``)."""

    KIND = "matching.profile"

    def __init__(
        self, uow: Callable[[], MatchUnitOfWork], profiles: ProfileSource, consents: ConsentSource,
        signals: VaultSignalSource, agents: MatchAgentsPort, vocabulary: Vocabulary, jobs: JobQueuePort,
        clock: Clock, cfg: MatchConfig,
    ) -> None:  # fmt: skip
        self._uow, self._profiles, self._consents, self._signals = uow, profiles, consents, signals
        self._agents, self._vocab, self._jobs, self._clock, self._cfg = agents, vocabulary, jobs, clock, cfg

    async def handle(self, payload: dict[str, Any], *, last_attempt: bool = False) -> dict[str, Any]:
        """Run for ``payload['account_id']``."""
        from uuid import UUID

        return await self.execute(AccountId(UUID(str(payload["account_id"]))), last_attempt=last_attempt)

    async def execute(self, account: AccountId, *, last_attempt: bool = False) -> dict[str, Any]:
        """Recompute and store the profile; enqueue a match run when it changed."""
        now = self._clock.now()
        consent = await self._consents.get(account)
        data = await self._profiles.matching_input(account) if consent and consent.matching else None
        if consent is None or not consent.matching or data is None:
            async with self._uow() as uow:
                await uow.profiles.delete(account)
                if consent is None or not consent.matching:
                    # Withdrawn consent closes pending proposals at once, so nobody is revealed afterwards.
                    for p in await uow.matches.involving_for_update(account):
                        if p.state in PENDING:
                            p.close_for_consent(p.side_of(account), now)
                            await uow.matches.save(p)
                await uow.commit()
            return {"status": "removed"}
        topics = compute_topics(data.works, now)
        works = method_works(data.works)
        sources = [(w.ref, (w.abstract or w.title)[:1500]) for w in works]
        refs = {ref: "abstract" for ref, _ in sources}
        use_vault = consent.vault_signals
        if use_vault:
            for s in await self._signals.signals(account, SIGNAL_SECTIONS, 10):
                sources.append((s.ref, s.text))
                refs[s.ref] = "vault"
        mh = methods_hash(sources, self._vocab.version, self._cfg.methods_prompt_version)
        async with self._uow() as uow:
            old = await uow.profiles.get(account)
        proposals: list[tuple[str, tuple[str, ...]]] = []
        if old is not None and old.methods_hash == mh:
            methods, method_sources = dict(old.methods), dict(old.method_sources)
        elif not sources:
            methods, method_sources = {}, {}
        else:
            try:
                out = await self._agents.extract_methods(
                    MethodExtractorInput(
                        vocabulary=[VocabularyEntry(method_id=t.id, label=t.label, synonyms=list(t.synonyms)) for t in self._vocab.terms],
                        sources=[MethodSource(source_ref=r, text=t) for r, t in sources],
                    ),
                    CallContext(feature="matching.profile", account_id=account, session_id=None, run_id=None),
                )  # fmt: skip
            except Exception:
                if not last_attempt:
                    raise
                if old is None:
                    return {"status": "failed"}
                methods, method_sources = dict(old.methods), dict(old.method_sources)
                mh = old.methods_hash
            else:
                weights = weigh_methods(out.output.methods, self._vocab, refs)
                methods = weights.effective(use_vault)
                method_sources = {
                    k: "vault"
                    if use_vault and weights.vault.get(k, 0) > weights.abstract.get(k, 0)
                    else "abstract"
                    for k in methods
                }
                proposals = weights.proposals
        profile = MatchProfile(
            account_id=account, researcher_id=data.researcher_id, field=consent.field, region=region_of(data.country).value,
            career_band=career_band(data.works, now).value, activity=activity(data.works, now),
            eligible=is_eligible(consent.matching, data.import_done, topics, methods), topics=tuple(topics),
            methods=methods, method_sources=method_sources, topics_hash=topics_hash(topics), methods_hash=mh,
            vocabulary_version=self._vocab.version, methods_prompt_version=self._cfg.methods_prompt_version,
            uses_vault_signals=use_vault, computed_at=now,
        )  # fmt: skip
        changed = old is None or (old.topics_hash, old.methods_hash, old.eligible) != (
            profile.topics_hash, profile.methods_hash, profile.eligible,
        )  # fmt: skip
        async with self._uow() as uow:
            await uow.profiles.save(profile, proposals)
            await uow.commit()
        if changed and profile.eligible:
            await self._jobs.enqueue(
                JobSpec(
                    kind="matching.compute",
                    payload={"account_id": str(account)},
                    dedupe_key=f"acct:{account}",
                    max_attempts=3,
                )
            )
        return {"status": "ok", "eligible": profile.eligible, "changed": changed}


__all__ = ["SIGNAL_SECTIONS", "ComputeResearcherProfile", "WorkIn"]
