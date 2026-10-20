"""Identity use cases: EnsureAccount, GetAccount, UpdateConsent."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from pydantic import JsonValue

from scout.identity.domain.account import Account, ConsentChanged
from scout.identity.ports.repos import IdentityUnitOfWork
from scout.ports.audit import AuditEntry
from scout.shared import (
    AccountId,
    Clock,
    DomainEvent,
    IdGenerator,
    NotFound,
    Unauthenticated,
    ValidationFailed,
)


class EmailRequired(Unauthenticated):
    """The access token carries no e-mail address."""

    code = "email_required"
    title = "E-mail address required"


class IdentityService:
    """Application service of the identity context."""

    def __init__(
        self, uow: Callable[[], IdentityUnitOfWork], clock: Clock, ids: IdGenerator, launch_field: str
    ) -> None:
        self._uow, self._clock, self._ids, self._launch_field = uow, clock, ids, launch_field

    async def _audit(
        self,
        uow: IdentityUnitOfWork,
        actor: UUID,
        action: str,
        target: str | None,
        meta: dict[str, JsonValue],
    ) -> None:
        await uow.audit.add(
            AuditEntry(
                id=self._ids.new(),
                actor_id=actor,
                action=action,
                target=target,
                at=self._clock.now(),
                meta=meta,
            )
        )

    async def ensure_account(self, sub: UUID, email: str | None) -> Account:
        """Return the account for ``sub``, creating it on the first call and syncing the e-mail."""
        if not email:
            raise EmailRequired()
        account_id = AccountId(sub)
        now = self._clock.now()
        async with self._uow() as uow:
            account = await uow.accounts.get(account_id)
            if account is None:
                account = Account.create(account_id, email, self._launch_field, now)
                if await uow.accounts.add_if_absent(account):
                    account.pull_events()
                    await self._audit(
                        uow, sub, "account.created", str(sub), {"email_domain": account.email.split("@")[-1]}
                    )
                    await uow.commit()
                    return account
                account = await uow.accounts.get(account_id)  # lost the race
                if account is None:  # pragma: no cover - defensive
                    raise NotFound("Account not found")
            old_domain = account.email.rsplit("@", 1)[-1]
            if account.sync_email(email, now):
                await uow.accounts.save(account)
                await self._audit(
                    uow, sub, "account.email_changed", str(sub),
                    {"from_domain": old_domain, "to_domain": account.email.rsplit("@", 1)[-1]},
                )  # fmt: skip
                await uow.commit()
            account.ensure_active()
            return account

    async def get_account(self, account_id: AccountId) -> Account:
        """Account or ``NotFound``."""
        async with self._uow() as uow:
            account = await uow.accounts.get(account_id)
        if account is None:
            raise NotFound("Account not found")
        return account

    async def update_consent(
        self, account_id: AccountId, matching: bool | None, vault_signals: bool | None
    ) -> Account:
        """Apply a consent patch; one audit row per flag that changed."""
        if matching is None and vault_signals is None:
            raise ValidationFailed("Nothing to update")
        async with self._uow() as uow:
            account = await uow.accounts.get(account_id)
            if account is None:
                raise NotFound("Account not found")
            account.ensure_active()
            before = (account.consent_matching, account.consent_vault_signals)
            account.set_consent(matching=matching, vault_signals=vault_signals, now=self._clock.now())
            events: list[DomainEvent] = account.pull_events()
            if events:
                await uow.accounts.save(account)
                for ev in events:
                    if isinstance(ev, ConsentChanged):
                        old = before[0] if ev.consent == "matching" else before[1]
                        await self._audit(
                            uow, account_id, "consent.changed", str(account_id),
                            {"consent": ev.consent, "from": old, "to": ev.value},
                        )  # fmt: skip
                await uow.commit()
        return account
