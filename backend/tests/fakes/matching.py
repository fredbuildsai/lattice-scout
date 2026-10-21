"""In-memory matching ports: repositories (copy on read, replace on save), sources, scripted agents, notifier."""

from __future__ import annotations

import copy
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from types import TracebackType
from typing import Self
from uuid import UUID

from scout.matching.domain.agent_io import (
    JointIdea,
    JointIdeaList,
    JointIdeasInput,
    MatchExplainerInput,
    MatchRationale,
    MethodExtractorInput,
    MethodSet,
    SignalItem,
)
from scout.matching.domain.enums import MatchState
from scout.matching.domain.profile import MatchProfile
from scout.matching.domain.proposal import MatchProposal
from scout.matching.domain.reveal import Contact
from scout.matching.ports.sources import Consent, ProfileInput
from scout.ports.llm import AgentOutcome, CallContext, TokenUsage
from scout.shared import AccountId

PENDING = (MatchState.PROPOSED, MatchState.A_ACCEPTED, MatchState.B_ACCEPTED)


@dataclass
class MatchStore:
    """Committed state shared by every unit of work of a test."""

    matches: dict[UUID, MatchProposal] = field(default_factory=dict)
    profiles: dict[AccountId, MatchProfile] = field(default_factory=dict)
    proposals: list[tuple[AccountId, str]] = field(default_factory=list)
    blocks: set[tuple[AccountId, AccountId]] = field(default_factory=set)
    ideas: dict[UUID, list[JointIdea]] = field(default_factory=dict)
    contacts: dict[AccountId, Contact] = field(default_factory=dict)
    conflicts: set[tuple[AccountId, AccountId, str]] = field(default_factory=set)


class FakeMatchRepo:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def add(self, proposal: MatchProposal) -> bool:
        active = any(
            (m.a_id, m.b_id) == (proposal.a_id, proposal.b_id) and m.state in (*PENDING, MatchState.MUTUAL)
            for m in self.s.matches.values()
        )
        if proposal.id in self.s.matches or active:
            return False
        self.s.matches[proposal.id] = copy.deepcopy(proposal)
        return True

    async def get(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        m = self.s.matches.get(match_id)
        return copy.deepcopy(m) if m and viewer in (m.a_id, m.b_id) else None

    async def get_for_update(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        return await self.get(match_id, viewer)

    async def involving_for_update(self, account: AccountId) -> list[MatchProposal]:
        return [copy.deepcopy(m) for m in self.s.matches.values() if account in (m.a_id, m.b_id)]

    async def delete(self, match_ids: Sequence[UUID]) -> None:
        for mid in match_ids:
            self.s.matches.pop(mid, None)
            self.s.ideas.pop(mid, None)

    async def purge_detached(self, closed_before: datetime) -> int:
        gone = [
            i for i, m in self.s.matches.items()
            if (m.a_id is None or m.b_id is None) and m.expires_at < closed_before
        ]  # fmt: skip
        await self.delete(gone)
        return len(gone)

    async def get_any(self, match_id: UUID, lock: bool = False) -> MatchProposal | None:
        m = self.s.matches.get(match_id)
        return copy.deepcopy(m) if m else None

    async def save(self, proposal: MatchProposal) -> None:
        self.s.matches[proposal.id] = copy.deepcopy(proposal)

    async def list_for(self, viewer: AccountId, limit: int, offset: int) -> list[MatchProposal]:
        rows = sorted(
            (m for m in self.s.matches.values() if viewer in (m.a_id, m.b_id)),
            key=lambda m: m.created_at,
            reverse=True,
        )
        return [copy.deepcopy(m) for m in rows[offset : offset + limit]]

    async def due_for_expiry(self, now: datetime, limit: int) -> list[UUID]:
        return [m.id for m in self.s.matches.values() if m.state in PENDING and m.expires_at <= now][:limit]

    async def count_created_since(self, account: AccountId, since: datetime) -> int:
        return sum(
            1 for m in self.s.matches.values() if account in (m.a_id, m.b_id) and m.created_at >= since
        )

    async def open_counts(self, accounts: Sequence[AccountId], since: datetime) -> dict[AccountId, int]:
        return {a: await self.count_created_since(a, since) for a in accounts}

    async def pending_outside_pool(self, limit: int) -> list[UUID]:
        pool = {a for a, p in self.s.profiles.items() if p.eligible}
        return [m.id for m in self.s.matches.values() if m.state in PENDING and not {m.a_id, m.b_id} <= pool][
            :limit
        ]

    async def ideas_pending_before(self, before: datetime, limit: int) -> list[UUID]:
        return [
            m.id
            for m in self.s.matches.values()
            if m.state is MatchState.MUTUAL
            and m.ideas_status.value == "pending"
            and (m.revealed_at or before) < before
        ][:limit]


class FakeRevealRepo:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def contact(self, match_id: UUID, viewer: AccountId) -> Contact | None:
        m = self.s.matches.get(match_id)
        if m is None or m.state is not MatchState.MUTUAL or viewer not in (m.a_id, m.b_id):
            return None
        other = m.b_id if viewer == m.a_id else m.a_id
        return self.s.contacts.get(other) if other is not None else None


class FakeBlockRepo:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def add(self, account: AccountId, blocked: AccountId, match_id: UUID, now: datetime) -> None:
        self.s.blocks.add((account, blocked))


class FakeProfileRepo:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def get(self, account: AccountId) -> MatchProfile | None:
        return self.s.profiles.get(account)

    async def get_many(self, accounts: Sequence[AccountId]) -> dict[AccountId, MatchProfile]:
        return {a: self.s.profiles[a] for a in accounts if a in self.s.profiles}

    async def save(self, profile: MatchProfile, proposals: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        self.s.profiles[AccountId(profile.account_id)] = profile
        self.s.proposals.extend((AccountId(profile.account_id), label) for label, _ in proposals)

    async def delete(self, account: AccountId) -> None:
        self.s.profiles.pop(account, None)

    async def accounts(self) -> list[AccountId]:
        return list(self.s.profiles)


class FakeCandidates:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def candidates(
        self, *, field: str, account: AccountId | None, exclude_coauthors: bool, exclude_same_group: bool,
        cooldown_since: datetime, limit: int, per_person: int = 50,
    ) -> list[tuple[AccountId, AccountId]]:  # fmt: skip
        pool = {a: p for a, p in self.s.profiles.items() if p.eligible and p.field == field}
        reasons = {r for r, on in (("coauthor", exclude_coauthors), ("same_group", exclude_same_group)) if on}
        out: list[tuple[float, AccountId, AccountId]] = []
        for a in sorted(pool):
            for b in sorted(pool):
                if not a < b or (account is not None and account not in (a, b)):
                    continue
                inter = sum(
                    min(t.weight, u.weight) for t in pool[a].topics for u in pool[b].topics if t.id == u.id
                )
                if inter <= 0 or any((a, b, r) in self.s.conflicts for r in reasons):
                    continue
                if (a, b) in self.s.blocks or (b, a) in self.s.blocks:
                    continue
                if any(
                    (m.a_id, m.b_id) == (a, b)
                    and (m.state in (*PENDING, MatchState.MUTUAL) or m.created_at > cooldown_since)
                    for m in self.s.matches.values()
                ):
                    continue
                out.append((-inter, a, b))
        ranked = sorted(out)
        seen: dict[AccountId, int] = {}
        keep: list[tuple[AccountId, AccountId]] = []
        for _, a, b in ranked:
            ra, rb = seen.get(a, 0), seen.get(b, 0)
            seen[a], seen[b] = ra + 1, rb + 1
            if ra < per_person or rb < per_person:
                keep.append((a, b))
        return keep[:limit]


class FakeIdeaRepo:
    def __init__(self, s: MatchStore) -> None:
        self.s = s

    async def add_many(
        self, match_id: UUID, ideas: Sequence[JointIdea], prompt_version: str, model: str, now: datetime
    ) -> None:
        if self.s.matches[match_id].state is not MatchState.MUTUAL:
            raise AssertionError("ideas for a non-mutual match")
        self.s.ideas.setdefault(match_id, list(ideas))

    async def list(self, match_id: UUID) -> list[JointIdea]:
        return list(self.s.ideas.get(match_id, []))


class FakeMatchUow:
    def __init__(self, s: MatchStore) -> None:
        self.s = s
        self.matches = FakeMatchRepo(s)
        self.reveal = FakeRevealRepo(s)
        self.blocks = FakeBlockRepo(s)
        self.profiles = FakeProfileRepo(s)
        self.candidates = FakeCandidates(s)
        self.ideas = FakeIdeaRepo(s)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class FakeSources:
    """ProfileSource, ConsentSource and VaultSignalSource in one."""

    def __init__(self) -> None:
        self.inputs: dict[AccountId, ProfileInput] = {}
        self.consents: dict[AccountId, Consent] = {}
        self.signal_items: dict[AccountId, list[SignalItem]] = {}

    async def matching_input(self, account: AccountId) -> ProfileInput | None:
        return self.inputs.get(account)

    async def get(self, account: AccountId) -> Consent | None:
        return self.consents.get(account)

    async def signals(self, account: AccountId, sections: tuple[str, ...], limit: int) -> list[SignalItem]:
        return self.signal_items.get(account, [])[:limit]


def outcome[T](value: T) -> AgentOutcome[T]:
    return AgentOutcome(value, "test", "fake", Decimal("0"), TokenUsage(input_tokens=1, output_tokens=1))


class ScriptedMatchAgents:
    """Callables decide each answer; counts calls. A missing script raises (like a missing fixture)."""

    def __init__(self) -> None:
        self.methods: Callable[[MethodExtractorInput], MethodSet] | None = None
        self.explainer: Callable[[MatchExplainerInput], MatchRationale] | None = None
        self.ideas: Callable[[JointIdeasInput], JointIdeaList] | None = None
        self.calls: dict[str, int] = {"methods": 0, "explain": 0, "ideas": 0}
        self.allowed = True

    def confidential_allowed(self, tier: object = None) -> bool:
        return self.allowed

    async def extract_methods(self, data: MethodExtractorInput, ctx: CallContext) -> AgentOutcome[MethodSet]:
        self.calls["methods"] += 1
        if self.methods is None:
            raise RuntimeError("no script")
        return outcome(self.methods(data))

    async def explain(self, data: MatchExplainerInput, ctx: CallContext) -> AgentOutcome[MatchRationale]:
        self.calls["explain"] += 1
        if self.explainer is None:
            raise RuntimeError("no script")
        return outcome(self.explainer(data))

    async def joint_ideas(self, data: JointIdeasInput, ctx: CallContext) -> AgentOutcome[JointIdeaList]:
        self.calls["ideas"] += 1
        if self.ideas is None:
            raise RuntimeError("no script")
        return outcome(self.ideas(data))


class RecordingNotifier:
    def __init__(self) -> None:
        self.proposed_to: list[tuple[AccountId, UUID]] = []
        self.mutual_to: list[tuple[AccountId, UUID]] = []

    async def proposed(self, account_id: AccountId, match_id: UUID) -> None:
        self.proposed_to.append((account_id, match_id))

    async def mutual(self, account_id: AccountId, match_id: UUID) -> None:
        self.mutual_to.append((account_id, match_id))
