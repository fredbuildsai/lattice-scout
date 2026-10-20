"""Identity persistence ports."""

from __future__ import annotations

from typing import Protocol

from scout.identity.domain.account import Account
from scout.ports.audit import AuditLog
from scout.shared import AccountId, UnitOfWork


class AccountRepo(Protocol):
    """Account storage."""

    async def get(self, account_id: AccountId) -> Account | None:
        """Account by id."""
        ...

    async def add_if_absent(self, account: Account) -> bool:
        """Insert unless the id exists (True if inserted); same e-mail, other id: ``Conflict``."""
        ...

    async def save(self, account: Account) -> None:
        """Persist changes."""
        ...


class IdentityUnitOfWork(UnitOfWork, Protocol):
    """Transaction for identity use cases."""

    @property
    def accounts(self) -> AccountRepo:
        """Account repository bound to this transaction."""
        ...

    @property
    def audit(self) -> AuditLog:
        """Audit log bound to this transaction."""
        ...
