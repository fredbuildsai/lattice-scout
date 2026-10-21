"""Matching use cases against fakes: profile job, compute, decide/reveal flow, ideas, nightly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.matching import FakeMatchUow, FakeSources, MatchStore, RecordingNotifier, ScriptedMatchAgents

from scout.matching.adapters.vocabulary import load_vocabulary
from scout.matching.application.compute import ComputeMatches
from scout.matching.application.config import MatchConfig
from scout.matching.application.profile_job import ComputeResearcherProfile
from scout.matching.application.reveal_job import MatchingNightly, RevealMatch
from scout.matching.application.service import ConsentRequired, DecideOnMatch, MatchingService
from scout.matching.domain.agent_io import (
    JointIdea,
    JointIdeaList,
    MatchRationale,
    MethodMention,
    MethodSet,
    Perspective,
)
from scout.matching.domain.enums import Action, CareerBand, IdeasStatus, MatchState, Region, ViewerStatus
from scout.matching.domain.profile import MatchProfile, TopicWeight, WorkIn
from scout.matching.domain.proposal import BringItem, MatchClosed, MatchExpired, MatchNotFound
from scout.matching.domain.reveal import AnonymousMatchView, Contact, RevealedMatchView
from scout.matching.ports.sources import Consent, ProfileInput
from scout.shared import AccountId, FixedClock, SequentialIdGenerator

VOCAB = load_vocabulary(Path(__file__).resolve().parents[4] / "config" / "methods.yaml")
NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
A, B, C = (AccountId(UUID(int=i)) for i in (1, 2, 3))


def profile(
    acc: AccountId, methods: dict[str, float], topics: tuple[str, ...] = ("t1", "t2", "t3")
) -> MatchProfile:
    return MatchProfile(
        account_id=acc, researcher_id=UUID(int=int(acc) + 100), field="bio-based-materials", region="europe",
        career_band="mid", activity=1.0, eligible=True, topics=tuple(TopicWeight(t, t.upper(), 1.0) for t in topics),
        methods=methods, method_sources={k: "abstract" for k in methods}, topics_hash="t", methods_hash="m",
        vocabulary_version="1", methods_prompt_version="1.0.1", uses_vault_signals=False, computed_at=NOW,
    )  # fmt: skip


class World:
    def __init__(self, cfg: MatchConfig | None = None) -> None:
        self.clock = FixedClock(NOW)
        self.store = MatchStore()
        self.uow = lambda: FakeMatchUow(self.store)
        self.sources = FakeSources()
        self.agents = ScriptedMatchAgents()
        self.notifier = RecordingNotifier()
        self.jobs = InMemoryJobQueue(self.clock, SequentialIdGenerator(1000))
        self.cfg = cfg or MatchConfig()
        self.compute = ComputeMatches(self.uow, self.agents, VOCAB, self.notifier, self.clock, self.cfg)
        self.decide = DecideOnMatch(self.uow, self.sources, self.jobs, self.notifier, self.clock, self.cfg)
        self.service = MatchingService(self.uow, self.sources, self.clock, self.cfg)
        self.reveal = RevealMatch(
            self.uow, self.sources, self.sources, self.sources, self.agents, VOCAB, self.clock
        )
        self.profile_job = ComputeResearcherProfile(
            self.uow,
            self.sources,
            self.sources,
            self.sources,
            self.agents,
            VOCAB,
            self.jobs,
            self.clock,
            self.cfg,
        )
        for acc, name in ((A, "Ada"), (B, "Bo"), (C, "Cy")):
            self.sources.consents[acc] = Consent(True, False, "bio-based-materials")
            self.store.contacts[acc] = Contact(
                display_name=name, institution="Example University", email=f"{name.lower()}@example.edu",
                orcid=None, openalex_ids=[], region=Region.EUROPE, career_band=CareerBand.MID,
            )  # fmt: skip

    def pair(self) -> None:
        self.store.profiles[A] = profile(A, {"lca": 1.0, "ftir": 0.5})
        self.store.profiles[B] = profile(B, {"tea": 1.0, "nmr": 0.5})

    async def proposed(self) -> UUID:
        self.pair()
        await self.compute.execute()
        (mid,) = self.store.matches
        return mid


# ---------------------------------------------------------------- profile job


def works(n: int = 4) -> tuple[WorkIn, ...]:
    return tuple(
        WorkIn(
            f"work:W{i}",
            f"Title {i}",
            f"abstract {i}",
            2026 - (i % 2),
            i,
            (("a", "A"), ("b", "B"), ("c", "C")),
        )
        for i in range(1, n + 1)
    )


async def test_profile_job_calls_the_model_once_per_input_hash_and_triggers_matching() -> None:
    w = World()
    w.sources.inputs[A] = ProfileInput(UUID(int=7), "DE", True, works())
    w.agents.methods = lambda _d: MethodSet(
        methods=[MethodMention(method_id="lca", source_refs=["work:W1", "work:W2"], confidence="high"),
                 MethodMention(method_id=None, new_label="odd thing", source_refs=["work:W1"], confidence="medium")]
    )  # fmt: skip
    first = await w.profile_job.execute(A)
    assert first == {"status": "ok", "eligible": True, "changed": True}
    assert w.store.profiles[A].methods == {"lca": 1.0} and w.store.profiles[A].region == "europe"
    assert w.store.proposals == [(A, "odd thing")]
    again = await w.profile_job.execute(A)
    assert again["changed"] is False and w.agents.calls["methods"] == 1
    assert [j.kind for j in w.jobs.jobs.values()] == ["matching.compute"]  # deduped, once


async def test_profile_job_removes_the_profile_without_consent_and_survives_model_failure_on_last_attempt() -> (
    None
):
    w = World()
    w.sources.inputs[A] = ProfileInput(UUID(int=7), "DE", True, works())
    w.store.profiles[A] = profile(A, {"lca": 1.0})
    w.sources.consents[A] = Consent(False, False, "bio-based-materials")
    assert await w.profile_job.execute(A) == {"status": "removed"} and A not in w.store.profiles
    w.sources.consents[A] = Consent(True, False, "bio-based-materials")
    with pytest.raises(RuntimeError):
        await w.profile_job.execute(A)  # no script: transient failure retries
    assert (await w.profile_job.execute(A, last_attempt=True)) == {"status": "failed"}


# ---------------------------------------------------------------- compute


async def test_compute_creates_one_template_proposal_and_a_rerun_adds_nothing() -> None:
    w = World()
    mid = await w.proposed()
    p = w.store.matches[mid]
    assert p.rationale.source == "template" and p.state is MatchState.PROPOSED and (p.a_id, p.b_id) == (A, B)
    assert [x[0] for x in w.notifier.proposed_to] == [A, B]
    again = await w.compute.execute()
    assert again["proposed"] == 0 and len(w.store.matches) == 1


async def test_compute_respects_the_coauthor_filter() -> None:
    w = World()
    w.store.conflicts.add((A, B, "coauthor"))
    w.pair()
    assert (await w.compute.execute())["proposed"] == 0
    off = World(MatchConfig(exclude_coauthors=False))
    off.store.conflicts.add((A, B, "coauthor"))
    off.pair()
    assert (await off.compute.execute())["proposed"] == 1


async def test_compute_keeps_only_complementary_pairs_and_applies_the_weekly_cap() -> None:
    w = World(MatchConfig(weekly_cap=1))
    w.store.profiles[A] = profile(A, {"lca": 1.0})
    w.store.profiles[B] = profile(B, {"lca": 1.0})  # same methods: nothing to bring
    assert (await w.compute.execute())["proposed"] == 0
    w.store.profiles[B] = profile(B, {"tea": 1.0})
    w.store.profiles[C] = profile(C, {"nmr": 1.0})
    assert (await w.compute.execute())["proposed"] == 1  # A-B, A-C, B-C compete; each account may take one


async def test_explainer_text_is_used_only_when_it_passes_the_anonymity_guard() -> None:
    w = World()
    good = Perspective(
        you_bring=[BringItem(text="Life-cycle thinking")],
        they_bring=[BringItem(text="Process economics")],
        why="Complementary methods on shared topics.",
    )
    w.agents.explainer = lambda _d: MatchRationale(for_a=good, for_b=good)
    mid = await w.proposed()
    assert w.store.matches[mid].rationale.source == "llm"
    leak = good.model_copy(update={"they_bring": [BringItem(text="Published in 2021 on this")]})
    w2 = World()
    w2.agents.explainer = lambda _d: MatchRationale(for_a=leak, for_b=leak)
    mid2 = await w2.proposed()
    assert w2.store.matches[mid2].rationale.source == "template"


# ---------------------------------------------------------------- decide, reveal, secrecy


async def test_double_opt_in_reveals_only_after_both_accept_and_enqueues_the_reveal_once() -> None:
    w = World()
    mid = await w.proposed()
    card = await w.decide.execute(A, mid, Action.ACCEPT)
    assert isinstance(card, AnonymousMatchView) and card.status is ViewerStatus.WAITING_ON_THEM
    other = await w.service.get(B, mid)
    assert isinstance(other, AnonymousMatchView) and other.status is ViewerStatus.AWAITING_YOU
    assert "Bo" not in other.model_dump_json() and "Ada" not in other.model_dump_json()
    revealed = await w.decide.execute(B, mid, Action.ACCEPT)
    assert isinstance(revealed, RevealedMatchView) and revealed.contact.display_name == "Ada"
    assert [j.kind for j in w.jobs.jobs.values()] == ["matching.reveal"]
    assert {x[0] for x in w.notifier.mutual_to} == {A, B}
    again = await w.decide.execute(B, mid, Action.ACCEPT)  # idempotent
    assert isinstance(again, RevealedMatchView) and len(w.jobs.jobs) == 1


async def test_a_decline_is_invisible_to_the_other_side() -> None:
    w = World()
    mid = await w.proposed()
    await w.decide.execute(A, mid, Action.ACCEPT)
    await w.decide.execute(B, mid, Action.DECLINE, never_similar=True)
    assert (B, A) in w.store.blocks
    seen = await w.service.get(A, mid)
    assert isinstance(seen, AnonymousMatchView) and seen.status is ViewerStatus.WAITING_ON_THEM
    assert "decline" not in seen.model_dump_json().lower().replace("my_decision", "")
    with pytest.raises(MatchNotFound):
        await w.service.get(B, mid)  # hidden for the decliner
    listed = await w.service.list(A)
    assert [c.id for c in listed.items] == [mid]
    assert (await w.service.list(B)).items == []
    w.clock.advance(timedelta(days=15))
    after = await w.service.get(A, mid)
    assert after.status is ViewerStatus.EXPIRED  # the only way A ever learns


async def test_decide_needs_consent_a_participant_and_persists_expiry() -> None:
    w = World()
    mid = await w.proposed()
    with pytest.raises(MatchNotFound):
        await w.decide.execute(C, mid, Action.ACCEPT)
    w.sources.consents[A] = Consent(False, False, "bio-based-materials")
    with pytest.raises(ConsentRequired):
        await w.decide.execute(A, mid, Action.ACCEPT)
    w.sources.consents[A] = Consent(True, False, "bio-based-materials")
    w.clock.advance(timedelta(days=15))
    with pytest.raises(MatchExpired):
        await w.decide.execute(A, mid, Action.ACCEPT)
    assert w.store.matches[mid].state is MatchState.EXPIRED  # saved despite the error
    with pytest.raises(MatchExpired):
        await w.decide.execute(B, mid, Action.DECLINE)


async def test_withdrawn_consent_closes_pending_proposals_at_once_and_nobody_is_revealed() -> None:
    w = World()
    mid = await w.proposed()
    await w.decide.execute(B, mid, Action.ACCEPT)
    w.sources.consents[B] = Consent(False, False, "bio-based-materials")
    w.sources.inputs[B] = ProfileInput(UUID(int=9), "DE", True, works())
    assert await w.profile_job.execute(B) == {"status": "removed"}
    assert w.store.matches[mid].state is MatchState.DECLINED  # closed straight away, not at 02:00
    card = await w.decide.execute(A, mid, Action.ACCEPT)  # A learns nothing: it simply never becomes mutual
    assert isinstance(card, AnonymousMatchView) and card.status is ViewerStatus.WAITING_ON_THEM
    assert w.jobs.jobs == {} and w.notifier.mutual_to == []


async def test_accept_does_not_become_mutual_when_the_counterpart_withdrew_before_the_profile_job_ran() -> (
    None
):
    w = World()
    mid = await w.proposed()
    await w.decide.execute(B, mid, Action.ACCEPT)
    w.sources.consents[B] = Consent(False, False, "bio-based-materials")  # job not run yet
    card = await w.decide.execute(A, mid, Action.ACCEPT)
    assert isinstance(card, AnonymousMatchView) and card.status is ViewerStatus.WAITING_ON_THEM
    assert w.store.matches[mid].state is not MatchState.MUTUAL and w.jobs.jobs == {}


async def test_accept_does_not_become_mutual_with_an_account_being_deleted() -> None:
    w = World()
    mid = await w.proposed()
    await w.decide.execute(B, mid, Action.ACCEPT)
    del w.sources.consents[B]  # ConsentSource answers None for a deleted account
    card = await w.decide.execute(A, mid, Action.ACCEPT)
    assert card.status is ViewerStatus.WAITING_ON_THEM and w.store.matches[mid].state is MatchState.DECLINED
    # the other order: A accepted first, then B (the leaver) is gone and A's own accept is already recorded
    w2 = World()
    mid2 = await w2.proposed()
    await w2.decide.execute(A, mid2, Action.ACCEPT)
    del w2.sources.consents[B]
    again = await w2.decide.execute(A, mid2, Action.ACCEPT)
    assert again.status is ViewerStatus.WAITING_ON_THEM


async def test_each_side_reads_the_explanation_written_for_it() -> None:
    w = World()
    a_text = Perspective(
        you_bring=[BringItem(text="Life-cycle thinking")], they_bring=[BringItem(text="Process economics")],
        why="Combining your life-cycle work with their economics.",
    )  # fmt: skip
    b_text = Perspective(
        you_bring=[BringItem(text="Process economics")], they_bring=[BringItem(text="Life-cycle thinking")],
        why="Combining your economics with their life-cycle work.",
    )  # fmt: skip
    w.agents.explainer = lambda _d: MatchRationale(for_a=a_text, for_b=b_text)
    mid = await w.proposed()
    ca, cb = await w.service.get(A, mid), await w.service.get(B, mid)
    assert ca.why == a_text.why and cb.why == b_text.why
    await w.decide.execute(A, mid, Action.ACCEPT)
    rb = await w.decide.execute(B, mid, Action.ACCEPT)
    ra = await w.service.get(A, mid)
    assert isinstance(rb, RevealedMatchView) and isinstance(ra, RevealedMatchView)
    assert (ra.why, rb.why) == (a_text.why, b_text.why)
    assert rb.contact.display_name == "Ada" and ra.contact.display_name == "Bo"  # both sides get contact


async def test_explainer_refs_are_dropped_and_a_leaky_b_text_falls_back_to_the_template() -> None:
    w = World()
    good = Perspective(
        you_bring=[BringItem(text="Life-cycle thinking", source_refs=["work:W1"])],
        they_bring=[BringItem(text="Process economics")], why="Complementary methods on shared topics.",
    )  # fmt: skip
    leaky_b = good.model_copy(update={"why": "See https://example.org for details"})
    w.agents.explainer = lambda _d: MatchRationale(for_a=good, for_b=leaky_b)
    mid = await w.proposed()
    r = w.store.matches[mid].rationale
    assert all(i.source_refs == [] for i in (*r.you_bring, *r.they_bring))
    assert "http" not in (await w.service.get(B, mid)).why


async def test_the_other_sides_vault_refs_are_never_shown() -> None:
    from scout.matching.application.service import _idea_view

    i = idea(["work:W1"], ["insight:doc-1.aims[0]"])
    v = _idea_view(i, viewer_is_a=True)
    assert v.uses_from_them[0].source_refs == [] and v.uses_from_you[0].source_refs == ["work:W1"]


async def mutual(w: World) -> UUID:
    mid = await w.proposed()
    await w.decide.execute(A, mid, Action.ACCEPT)
    await w.decide.execute(B, mid, Action.ACCEPT)
    return mid


def idea(refs_a: list[str], refs_b: list[str], title: str = "Joint foam durability study") -> JointIdea:
    return JointIdea(
        title=title, question="Can combined methods improve durability?", first_step="Share one dataset",
        possible_output="Short paper", uses_from_a=[BringItem(text="Her data", source_refs=refs_a)],
        uses_from_b=[BringItem(text="His data", source_refs=refs_b)],
    )  # fmt: skip


async def test_reveal_generates_validated_ideas_once_and_the_card_shows_them() -> None:
    w = World()
    mid = await mutual(w)
    w.sources.inputs[A] = ProfileInput(UUID(int=7), "DE", True, works(2))
    w.sources.inputs[B] = ProfileInput(UUID(int=8), "FR", True, works(2))
    w.agents.ideas = lambda _d: JointIdeaList(
        ideas=[idea(["work:W1"], ["work:W2"]), idea(["ghost"], ["work:W1"], "Bad refs")]
    )
    res = await w.reveal.handle({"match_id": str(mid)})
    assert res == {"status": "ready", "ideas": 1}
    card = await w.service.get(A, mid)
    assert (
        isinstance(card, RevealedMatchView)
        and card.ideas_status is IdeasStatus.READY
        and len(card.joint_ideas) == 1
    )
    assert (await w.reveal.handle({"match_id": str(mid)})) == {"status": "ready"} and w.agents.calls[
        "ideas"
    ] == 1


async def test_reveal_skips_non_mutual_and_fails_softly_on_the_last_attempt() -> None:
    w = World()
    pid = await w.proposed()
    assert await w.reveal.handle({"match_id": str(pid)}) == {"status": "skipped"}
    assert w.agents.calls["ideas"] == 0
    w2 = World()
    mid = await mutual(w2)
    w2.agents.ideas = lambda _d: JointIdeaList(ideas=[idea(["ghost"], ["ghost"])])
    with pytest.raises(Exception):  # noqa: B017
        await w2.reveal.handle({"match_id": str(mid)})
    assert (await w2.reveal.handle({"match_id": str(mid)}, last_attempt=True)) == {"status": "failed"}
    card = await w2.service.get(A, mid)
    assert (
        isinstance(card, RevealedMatchView) and card.ideas_status is IdeasStatus.FAILED
    )  # contact still shown


# ---------------------------------------------------------------- nightly


async def test_nightly_expires_closes_for_consent_and_schedules_itself() -> None:
    w = World()
    mid = await w.proposed()
    night = MatchingNightly(w.uow, w.jobs, w.profile_job, w.compute, w.clock, w.cfg)
    w.sources.consents[A] = Consent(False, False, "bio-based-materials")
    w.store.profiles[A] = profile(A, {"lca": 1.0}).__class__(
        **{**profile(A, {"lca": 1.0}).__dict__, "eligible": False}
    )
    res = await night.handle({})
    assert res["closed"] == 1 and w.store.matches[mid].state is MatchState.DECLINED
    assert any(j.kind == "matching.nightly" for j in w.jobs.jobs.values())
    w2 = World()
    mid2 = await w2.proposed()
    w2.clock.advance(timedelta(days=15))
    n2 = MatchingNightly(w2.uow, w2.jobs, w2.profile_job, w2.compute, w2.clock, w2.cfg)
    assert (await n2.handle({}))["expired"] == 1 and w2.store.matches[mid2].state is MatchState.EXPIRED


async def test_closed_matches_reject_further_actions() -> None:
    w = World()
    mid = await w.proposed()
    await w.decide.execute(A, mid, Action.DECLINE)
    with pytest.raises(MatchClosed):
        await w.decide.execute(A, mid, Action.ACCEPT)
