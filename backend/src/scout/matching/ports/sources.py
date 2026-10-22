"""What matching needs from other contexts (implemented by the anti-corruption layer)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from scout.matching.domain.agent_io import SignalItem
from scout.matching.domain.profile import WorkIn
from scout.shared import AccountId


@dataclass(frozen=True)
class ProfileInput:
    """A claimed profile as the matching profile job needs it."""

    researcher_id: UUID
    country: str | None
    import_done: bool
    works: tuple[WorkIn, ...]  # included works


@dataclass(frozen=True)
class Consent:
    """The account's consent flags and field."""

    matching: bool
    vault_signals: bool
    field: str


class ProfileSource(Protocol):
    """Claimed profile."""

    async def matching_input(self, account: AccountId) -> ProfileInput | None:
        """Included works and facets, or None without a profile."""
        ...


class ConsentSource(Protocol):
    """Consent flags."""

    async def get(self, account: AccountId) -> Consent | None:
        """Consent of an active account, or None."""
        ...


class VaultSignalSource(Protocol):
    """Verified vault insights, offered only when the owner consented."""

    async def signals(self, account: AccountId, sections: tuple[str, ...], limit: int) -> list[SignalItem]:
        """Verified items of the given sections (``ref`` is ``insight:<document>.<section>[n]``)."""
        ...
