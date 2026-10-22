"""Ports used by account erasure. Bootstrap wires implementations from other contexts (structural typing)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from scout.identity.domain.erasure import ErasureSubject
from scout.ports.audit import AuditEntry


class ForgetCounts(Protocol):
    """What the matching context reports back."""

    @property
    def detached(self) -> int:
        """Pending proposals that now exist only for the counterpart."""
        ...

    @property
    def deleted(self) -> int:
        """Mutual proposals removed."""
        ...


class SubjectInventory(Protocol):
    """Collect every id that belongs to an account."""

    async def collect(self, account_id: UUID) -> ErasureSubject:
        """Inventory; an unknown account gives a subject with the account id only."""
        ...


class MatchErasurePort(Protocol):
    """Detach pending proposals and delete mutual ones, in one transaction."""

    async def forget(self, account_id: UUID, now: datetime) -> ForgetCounts:
        """Idempotent."""
        ...


class ObservabilityErasure(Protocol):
    """Delete analytics rows and anonymise usage rows."""

    async def erase(self, subject: ErasureSubject) -> dict[str, int]:
        """Counts per table kind."""
        ...


class AccountFinalizer(Protocol):
    """The single transaction that deletes jobs, audit rows and the account itself."""

    async def finish(self, subject: ErasureSubject, own_job_id: UUID, entry: AuditEntry) -> dict[str, int]:
        """Idempotent; writes ``entry`` once."""
        ...


class AuthAdminPort(Protocol):
    """Remove the user from the authentication provider."""

    async def delete_user(self, user_id: UUID) -> None:
        """Idempotent: an unknown user is success."""
        ...
