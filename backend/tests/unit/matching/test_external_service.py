"""``ExternalSuggestions`` against fakes: refresh (scopes, exclusions, storage, no repeats), daily limit, profile
gate, failed OpenAlex calls, state changes, and erasure."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from tests.fakes.literature import InMemoryLiterature
from tests.fakes.matching import FakeMatchUow, FakeMembers, FakeSources, MatchStore

from scout.matching.application.config import ExternalConfig
from scout.matching.application.erasure import MatchErasure
from scout.matching.application.external import (
    ExternalRefreshLimit,
    ExternalSuggestions,
    ProfileRequired,
    SuggestionNotFound,
)
from scout.matching.domain.external import ExternalLayer, SuggestionState
from scout.matching.domain.profile import MatchProfile, TopicWeight, WorkIn
from scout.matching.ports.sources import ProfileInput
from scout.ports.literature import AuthorRecord, TopicRef, WorkFilter, WorkRecord, YearCount
from scout.shared import AccountId, FixedClock, SequentialIdGenerator

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
ME, OTHER = AccountId(UUID(int=1)), AccountId(UUID(int=2))
NET, FIELD = ExternalLayer.IN_NETWORK, ExternalLayer.IN_FIELD


def w(wid: str, *authors: str, last: str | None = None, year: int = 2025) -> WorkRecord:
    return WorkRecord(
        id=wid, title=wid, year=year, author_ids=authors, author_names=tuple(f"Name {a}" for a in authors),
        last_author_id=last, last_author_name=f"Name {last}" if last else None,
    )  # fmt: skip


def rec(aid: str, topics: dict[str, int], recent: int = 4) -> AuthorRecord:
    return AuthorRecord(
        id=aid, display_name=f"Name {aid}", institutions=(f"Uni {aid}",), h_index=10, cited_by_count=500,
        works_count=60, topics=tuple(TopicRef(id=t, name=f"Topic {t}", count=c) for t, c in topics.items()),
        counts_by_year=(YearCount(year=2026, works_count=recent),),
    )  # fmt: skip


def match_profile(topics: dict[str, float]) -> MatchProfile:
    return MatchProfile(
        account_id=ME, researcher_id=UUID(int=101), field="bio-based-materials", region="europe", career_band="mid",
        activity=1.0, eligible=True, topics=tuple(TopicWeight(k, f"My {k}", v) for k, v in topics.items()),
        methods={}, method_sources={}, topics_hash="t", methods_hash="m", vocabulary_version="1",
        methods_prompt_version="1.0.1", uses_vault_signals=False, computed_at=NOW,
    )  # fmt: skip


class World:
    def __init__(self, cfg: ExternalConfig | None = None) -> None:
        self.clock = FixedClock(NOW)
        self.store = MatchStore()
        self.sources = FakeSources()
        self.members = FakeMembers({"MEMBER"})
        self.lit = InMemoryLiterature()
        self.svc = ExternalSuggestions(
            lambda: FakeMatchUow(self.store), self.sources, self.members, self.lit, self.clock,
            SequentialIdGenerator(1), cfg or ExternalConfig(),
        )  # fmt: skip
        self.sources.inputs[ME] = ProfileInput(UUID(int=101), "DE", True, (), ("ME",))
        self.store.profiles[ME] = match_profile({"T1": 1.0, "T2": 0.8})
        # My works: C1 three times, C2 once; DIRECT is a co-author too.
        self.lit.works_by_author["ME"] = [
            w("W1", "ME", "C1", last="ME"),
            w("W2", "C1", "ME", "DIRECT"),
            w("W3", "ME", "C1", "C2"),
        ]
        # C1's recent works: P1 (last author) is a co-author of a co-author; MID is a middle author.
        self.lit.works_by_author["C1"] = [
            w("W10", "C1", "MID", "P1", last="P1"),
            w("W11", "C1", "ME", last="ME"),
        ]
        self.lit.works_by_author["C2"] = [
            w("W20", "C2", "P1", last="P1"),
            w("W21", "C2", "MEMBER", last="MEMBER"),
        ]
        # Topical works: F1 and F2 lead; P1 appears here too; DIRECT is my co-author.
        self.lit.topical = [
            w("W30", "X", "F1", last="F1"),
            w("W31", "Y", "P1", last="P1"),
            w("W32", "Z", "DIRECT", last="DIRECT"),
            w("W33", "Z", "F2", last="F2"),
            w("W34", "Z", "FOFF", last="FOFF"),
        ]
        self.lit.authors = {
            "P1": rec("P1", {"T1": 2, "T9": 2}),
            "F1": rec("F1", {"T1": 1, "T2": 1}),
            "F2": rec("F2", {"T2": 1, "T7": 3}, recent=0),
            "FOFF": rec("FOFF", {"T9": 1}),  # off topic: dropped
        }


async def test_refresh_finds_network_and_field_candidates_with_exclusions() -> None:
    world = World()
    got = await world.svc.refresh(ME, [NET, FIELD])
    assert [(s.openalex_id, s.layer) for s in got] == [("P1", NET), ("F1", FIELD), ("F2", FIELD)]
    p1 = got[0]
    assert set(p1.vias) == {"Name C1", "Name C2"} and p1.institution == "Uni P1" and p1.h_index == 10
    assert p1.shared_topics == ("Topic T1",) and p1.formula == "ext1" and p1.recent_works == 4
    assert got[1].score > got[2].score  # F2 is inactive and shares less
    looked_up = next(arg for name, arg in world.lit.calls if name == "get_authors")
    assert isinstance(looked_up, tuple)
    # Never looked up: myself, my co-authors, members, middle authors.
    assert {"ME", "C1", "C2", "DIRECT", "MEMBER", "MID"}.isdisjoint(looked_up)
    topical = [
        a for n, a in world.lit.calls if n == "works_by_filter" and isinstance(a, WorkFilter) and a.topic_ids
    ]
    assert topical and topical[0].topic_ids == ("T1", "T2") and topical[0].sort == "cited_by_count:desc"
    assert topical[0].from_publication_date == "2023-11-17" and [f.page for f in topical] == [1, 2]
    assert len(world.store.external) == 3


async def test_refresh_respects_scopes_and_never_repeats() -> None:
    world = World()
    got = await world.svc.refresh(ME, [FIELD])
    assert {s.layer for s in got} == {FIELD}
    assert "P1" in {s.openalex_id for s in got}  # without the network search P1 is only in the field
    assert not any(isinstance(a, WorkFilter) and a.author_id for _, a in world.lit.calls)
    again = await world.svc.refresh(ME, [NET, FIELD])
    assert sorted(s.openalex_id for s in again) == sorted(s.openalex_id for s in got)  # seen ones are skipped
    assert len(world.store.external) == len(got)


async def test_dismissed_are_hidden_and_never_suggested_again() -> None:
    world = World()
    got = await world.svc.refresh(ME, [NET, FIELD])
    p1 = got[0]
    dismissed = await world.svc.set_state(ME, p1.id, SuggestionState.DISMISSED)
    assert dismissed.state is SuggestionState.DISMISSED and dismissed.updated_at == NOW
    assert p1.id not in {s.id for s in await world.svc.visible(ME)}
    await world.svc.refresh(ME, [NET])
    assert [s.openalex_id for s in world.store.external.values()].count("P1") == 1
    saved = await world.svc.set_state(ME, got[1].id, SuggestionState.SAVED)
    assert saved.state is SuggestionState.SAVED
    assert [s.openalex_id for s in await world.svc.visible(ME, FIELD)] == ["F1", "F2"]
    with pytest.raises(SuggestionNotFound):
        await world.svc.set_state(OTHER, got[1].id, SuggestionState.SAVED)  # not yours


async def test_daily_limit() -> None:
    world = World(ExternalConfig(refresh_per_day=2))
    await world.svc.refresh(ME, [NET])
    await world.svc.refresh(ME, [NET])
    with pytest.raises(ExternalRefreshLimit):
        await world.svc.refresh(ME, [NET])
    world.clock.advance(timedelta(days=1, seconds=1))
    await world.svc.refresh(ME, [NET])


async def test_profile_required_and_topics_from_works_without_a_matching_profile() -> None:
    world = World()
    with pytest.raises(ProfileRequired):
        await world.svc.refresh(OTHER, [NET])
    del world.store.profiles[ME]
    works = tuple(
        WorkIn(ref=f"work:W{i}", title="t", abstract=None, year=2025, cited_by=1, topics=(("T1", "Lignin"),))
        for i in range(3)
    )
    world.sources.inputs[ME] = ProfileInput(UUID(int=101), "DE", True, works, ("ME",))
    got = await world.svc.refresh(ME, [FIELD])
    assert "F1" in {s.openalex_id for s in got}


async def test_failed_openalex_calls_do_not_sink_the_refresh() -> None:
    world = World()
    world.lit.fail = {"get_authors"}
    assert await world.svc.refresh(ME, [NET, FIELD]) == []
    world.lit.fail = {"works_by_filter"}
    assert await world.svc.refresh(ME, [NET, FIELD]) == []


async def test_erasure_deletes_the_accounts_suggestions_only() -> None:
    world = World()
    await world.svc.refresh(ME, [NET, FIELD])
    world.sources.inputs[OTHER] = ProfileInput(UUID(int=102), "DE", True, (), ("ME",))
    world.store.profiles[OTHER] = match_profile({"T1": 1.0})
    await world.svc.refresh(OTHER, [FIELD])
    before_other = sum(1 for s in world.store.external.values() if s.account_id == OTHER)
    assert before_other > 0
    result = await MatchErasure(lambda: FakeMatchUow(world.store)).forget(ME, NOW)
    assert result.external_deleted == 3
    assert {s.account_id for s in world.store.external.values()} == {OTHER}
    assert all(a != ME for a, _ in world.store.refreshes)
