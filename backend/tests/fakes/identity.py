"""In-memory identity repositories and unit of work (transactional: changes apply on commit)."""

from __future__ import annotations

import copy
from datetime import datetime
from types import TracebackType
from typing import Self

from scout.identity.domain.account import Account
from scout.shared import AccountId, Conflict
from tests.fakes.audit import InMemoryAuditLog


class IdentityStore:
    """Committed state shared by all unit-of-work instances."""

    def __init__(self) -> None:
        self.accounts: dict[AccountId, Account] = {}
        self.audit = InMemoryAuditLog()


class InMemoryAccountRepo:
    def __init__(self, data: dict[AccountId, Account]) -> None:
        self.data = data

    async def get(self, account_id: AccountId) -> Account | None:
        return copy.deepcopy(self.data.get(account_id))

    async def add_if_absent(self, account: Account) -> bool:
        if account.id in self.data:
            return False
        if any(a.email == account.email for a in self.data.values()):
            raise Conflict("E-mail already used", extra={"code": "email_in_use"})
        self.data[account.id] = copy.deepcopy(account)
        return True

    async def save(self, account: Account) -> None:
        self.data[account.id] = copy.deepcopy(account)

    async def get_for_update(self, account_id: AccountId) -> Account | None:
        return await self.get(account_id)

    async def deleted_at_for(self, subject_hash: str) -> datetime | None:
        return None


class InMemoryIdentityUoW:
    """Stages changes in copies; ``commit`` publishes them to the store."""

    def __init__(self, store: IdentityStore) -> None:
        self._store = store
        self.committed = False

    async def __aenter__(self) -> Self:
        self._staged = {k: copy.deepcopy(v) for k, v in self._store.accounts.items()}
        self.accounts = InMemoryAccountRepo(self._staged)
        self.audit = InMemoryAuditLog()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        self._store.accounts.clear()
        self._store.accounts.update(self._staged)
        self._store.audit.entries.extend(self.audit.entries)
        self.committed = True

    async def rollback(self) -> None:
        return None
