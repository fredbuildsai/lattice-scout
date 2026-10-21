"""Matching persistence ports."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

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
