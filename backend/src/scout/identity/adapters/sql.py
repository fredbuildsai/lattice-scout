"""SQL repositories for the identity context."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence.audit import SqlAuditLog
from scout.adapters.persistence.models import account as t
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.identity.domain.account import Account, AccountRole, EmailInUse
from scout.ports.audit import AuditLog
from scout.shared import AccountId


def _to_entity(row: object) -> Account:
    m = row._mapping  # type: ignore[attr-defined]
    return Account(
        id=AccountId(m["id"]), email=m["email"], launch_field=m["launch_field"], created_at=m["created_at"],
        updated_at=m["updated_at"], consent_matching=m["consent_matching"],
        consent_vault_signals=m["consent_vault_signals"], role=AccountRole(m["role"]), deleted_at=m["deleted_at"],
    )  # fmt: skip


def _values(a: Account) -> dict[str, object]:
    return {
        "id": a.id, "email": a.email, "consent_matching": a.consent_matching,
        "consent_vault_signals": a.consent_vault_signals, "launch_field": a.launch_field, "role": a.role.value,
        "deleted_at": a.deleted_at, "created_at": a.created_at, "updated_at": a.updated_at,
    }  # fmt: skip


class SqlAccountRepo:
    """Implements ``AccountRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, account_id: AccountId) -> Account | None:
        """Account by id."""
        row = (await self._s.execute(select(t).where(t.c.id == account_id))).first()
        return _to_entity(row) if row else None

    async def add_if_absent(self, account: Account) -> bool:
        """INSERT ... ON CONFLICT (id) DO NOTHING; another id with the same e-mail raises ``EmailInUse``."""
        try:
            async with self._s.begin_nested():
                res = await self._s.execute(
                    pg_insert(t)
                    .values(**_values(account))
                    .on_conflict_do_nothing(index_elements=[t.c.id])
                    .returning(t.c.id)
                )
        except IntegrityError as exc:
            raise EmailInUse("This e-mail address belongs to another account") from exc
        return res.first() is not None

    async def save(self, account: Account) -> None:
        """Update mutable fields."""
        vals = _values(account)
        vals.pop("id")
        vals.pop("created_at")
        await self._s.execute(update(t).where(t.c.id == account.id).values(**vals))


class SqlIdentityUnitOfWork(SqlUnitOfWork):
    """Identity transaction."""

    accounts: SqlAccountRepo
    audit: AuditLog

    def _bind(self) -> None:
        self.accounts = SqlAccountRepo(self.session)
        self.audit = SqlAuditLog(self.session)
