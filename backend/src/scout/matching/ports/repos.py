"""Matching persistence ports."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from scout.matching.domain.agent_io import JointIdea
from scout.matching.domain.profile import MatchProfile
from scout.matching.domain.proposal import MatchProposal
from scout.matching.domain.reveal import Contact
from scout.shared import AccountId, UnitOfWork


class MatchRepo(Protocol):
    """Proposals."""

    async def add(self, proposal: MatchProposal) -> bool:
        """Insert. False when the id or the active pair already exists (nothing is written)."""
        ...

    async def get(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        """The proposal when ``viewer`` is a participant, else None."""
        ...

    async def get_for_update(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        """Like ``get``, with the row locked until the transaction ends."""
        ...

    async def save(self, proposal: MatchProposal) -> None:
        """Persist state, decisions and idea status."""
        ...

    async def list_for(self, viewer: AccountId, limit: int, offset: int) -> list[MatchProposal]:
        """The viewer's proposals, newest first."""
        ...

    async def due_for_expiry(self, now: datetime, limit: int) -> list[UUID]:
        """Ids of pending proposals past their deadline."""
        ...

    async def count_created_since(self, account: AccountId, since: datetime) -> int:
        """Proposals created for ``account`` since ``since``."""
        ...

    async def open_counts(self, accounts: Sequence[AccountId], since: datetime) -> dict[AccountId, int]:
        """Proposals created for each account since ``since`` (for the weekly cap)."""
        ...

    async def pending_outside_pool(self, limit: int) -> list[UUID]:
        """Pending proposals where a party left the matching pool (consent withdrawn or account gone)."""
        ...

    async def ideas_pending_before(self, before: datetime, limit: int) -> list[UUID]:
        """Mutual matches whose ideas are still pending since before ``before``."""
        ...

    async def get_any(self, match_id: UUID, lock: bool = False) -> MatchProposal | None:
        """Worker side: any participant, optionally locked."""
        ...

    async def involving_for_update(self, account: AccountId) -> list[MatchProposal]:
        """Every proposal ``account`` takes part in, locked (account erasure)."""
        ...

    async def delete(self, match_ids: Sequence[UUID]) -> None:
        """Remove proposals (their joint ideas go with them)."""
        ...

    async def purge_detached(self, closed_before: datetime) -> int:
        """Delete detached proposals whose deadline passed before ``closed_before``; return how many."""
        ...


class MatchProfileRepo(Protocol):
    """Matching profiles."""

    async def get(self, account: AccountId) -> MatchProfile | None:
        """The stored profile."""
        ...

    async def get_many(self, accounts: Sequence[AccountId]) -> dict[AccountId, MatchProfile]:
        """Stored profiles by account."""
        ...

    async def save(self, profile: MatchProfile, proposals: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        """Replace the profile, its topics and methods; remember proposed new method labels."""
        ...

    async def delete(self, account: AccountId) -> None:
        """Remove the profile (consent withdrawn)."""
        ...

    async def accounts(self) -> list[AccountId]:
        """Accounts that have a profile."""
        ...


class CandidateQuery(Protocol):
    """Candidate pairs by topic overlap, with the exclusions applied in SQL."""

    async def candidates(
        self,
        *,
        field: str,
        account: AccountId | None,
        exclude_coauthors: bool,
        exclude_same_group: bool,
        cooldown_since: datetime,
        limit: int,
        per_person: int = 50,
    ) -> list[tuple[AccountId, AccountId]]:
        """Canonical pairs (``a < b``) best overlap first, at most ``per_person`` best pairs for each person. Excludes blocked pairs, pairs with a pending or mutual
        proposal and pairs proposed since ``cooldown_since``."""
        ...


class JointIdeaRepo(Protocol):
    """Joint ideas of mutual matches."""

    async def add_many(
        self, match_id: UUID, ideas: Sequence[JointIdea], prompt_version: str, model: str, now: datetime
    ) -> None:
        """Insert (idempotent on match and position)."""
        ...

    async def list(self, match_id: UUID) -> list[JointIdea]:
        """Ideas in order."""
        ...


class RevealRepo(Protocol):
    """Identity and contact of the other side. Reads only ``match_reveal_v``."""

    async def contact(self, match_id: UUID, viewer: AccountId) -> Contact | None:
        """Contact of the other side, or None unless the match is mutual."""
        ...


class BlockRepo(Protocol):
    """Never-similar blocks."""

    async def add(self, account: AccountId, blocked: AccountId, match_id: UUID, now: datetime) -> None:
        """Remember that ``account`` never wants to be matched with ``blocked``."""
        ...


class MatchUnitOfWork(UnitOfWork, Protocol):
    """One transaction over the matching repositories."""

    @property
    def matches(self) -> MatchRepo:
        """Proposals."""
        ...

    @property
    def reveal(self) -> RevealRepo:
        """Contacts."""
        ...

    @property
    def blocks(self) -> BlockRepo:
        """Blocks."""
        ...

    @property
    def profiles(self) -> MatchProfileRepo:
        """Profiles."""
        ...

    @property
    def candidates(self) -> CandidateQuery:
        """Candidate query."""
        ...

    @property
    def ideas(self) -> JointIdeaRepo:
        """Joint ideas."""
        ...
