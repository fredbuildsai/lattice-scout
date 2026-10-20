"""Profile persistence ports."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol
from uuid import UUID

from scout.ports.audit import AuditLog
from scout.profile.domain.researcher import Researcher, VerifiedLevel
from scout.profile.domain.work import ClaimedWork
from scout.shared import AccountId, UnitOfWork


class ResearcherRepo(Protocol):
    """Researcher storage (children: claimed OpenAlex ids and topics)."""

    async def get_by_account(self, account_id: AccountId) -> Researcher | None:
        """The researcher of an account."""
        ...

    async def get(self, researcher_id: UUID) -> Researcher | None:
        """A researcher by id."""
        ...

    async def get_for_update(self, researcher_id: UUID) -> Researcher | None:
        """Like ``get`` but locks the row until the transaction ends (serialises writers per researcher)."""
        ...

    async def lock_authors(self, author_ids: Collection[str]) -> None:
        """Take transaction-scoped locks on OpenAlex author ids so concurrent claims of the same id serialise."""
        ...

    async def find_by_author_ids(self, author_ids: Collection[str]) -> list[Researcher]:
        """Every researcher (any account) that claimed one of the ids."""
        ...

    async def set_verified_level(self, researcher_id: UUID, level: VerifiedLevel) -> None:
        """Change only ``verified_level`` (used for other accounts' rows: never rewrites their import state)."""
        ...

    async def add(self, researcher: Researcher) -> None:
        """Insert."""
        ...

    async def save(self, researcher: Researcher) -> None:
        """Persist the aggregate, replacing child rows."""
        ...


class ClaimedWorkRepo(Protocol):
    """Claimed work storage."""

    async def list_for(self, researcher_id: UUID) -> list[ClaimedWork]:
        """All works of a researcher."""
        ...

    async def get(self, work_id: UUID, researcher_id: UUID) -> ClaimedWork | None:
        """One work, only if it belongs to the researcher."""
        ...

    async def upsert_many(self, works: list[ClaimedWork]) -> None:
        """Insert or update on ``(researcher_id, openalex_work_id)``."""
        ...

    async def save(self, work: ClaimedWork) -> None:
        """Persist one work."""
        ...

    async def delete_unsourced(self, researcher_id: UUID, keep_author_ids: Collection[str]) -> int:
        """Drop source ids that are no longer claimed; delete works left without any source."""
        ...


class ProfileUnitOfWork(UnitOfWork, Protocol):
    """Transaction for profile use cases."""

    @property
    def researchers(self) -> ResearcherRepo:
        """Researcher repository."""
        ...

    @property
    def works(self) -> ClaimedWorkRepo:
        """Claimed work repository."""
        ...

    @property
    def audit(self) -> AuditLog:
        """Audit log."""
        ...
