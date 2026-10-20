"""Account aggregate: identity, consents and lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from scout.shared import AccountId, AggregateRoot, DomainEvent, Forbidden, InvariantViolation
from scout.shared.ids import UuidGenerator


class AccountRole(StrEnum):
    """Application role (admin views arrive in M7)."""

    USER = "user"
    ADMIN = "admin"


class AccountCreated(DomainEvent):
    """A new account was created on first authenticated call."""

    name: ClassVar[str] = "identity.account_created"
    account_id: AccountId
    email_domain: str


class ConsentChanged(DomainEvent):
    """One consent flag really changed."""

    name: ClassVar[str] = "identity.consent_changed"
    account_id: AccountId
    consent: Literal["matching", "vault_signals"]
    value: bool


class ConsentRequiresMatching(InvariantViolation):
    """Vault signals cannot be shared without matching consent."""

    code = "consent_requires_matching"
    title = "Vault signals require matching consent"


class AccountDeleted(Forbidden):
    """The account was deleted."""

    code = "account_deleted"
    title = "Account deleted"


@dataclass
class Account(AggregateRoot):
    """A user account. ``consent_vault_signals`` implies ``consent_matching``."""

    id: AccountId
    email: str
    launch_field: str
    created_at: datetime
    updated_at: datetime
    consent_matching: bool = False
    consent_vault_signals: bool = False
    role: AccountRole = AccountRole.USER
    deleted_at: datetime | None = None

    @classmethod
    def create(cls, id: AccountId, email: str, launch_field: str, now: datetime) -> Account:
        """New account with both consents off; records ``AccountCreated``."""
        acct = cls(
            id=id, email=email.strip().lower(), launch_field=launch_field, created_at=now, updated_at=now
        )
        acct.record_event(
            AccountCreated(
                event_id=UuidGenerator().new(),
                occurred_at=now,
                account_id=id,
                email_domain=acct.email.rsplit("@", 1)[-1],
            )
        )
        return acct

    def set_consent(self, *, matching: bool | None, vault_signals: bool | None, now: datetime) -> None:
        """Apply a consent patch. Turning matching off also turns vault signals off."""
        new_matching = self.consent_matching if matching is None else matching
        new_vault = self.consent_vault_signals if vault_signals is None else vault_signals
        if matching is False:
            new_vault = False
        if new_vault and not new_matching:
            raise ConsentRequiresMatching("Vault signals can only be shared when matching is allowed")
        changes: list[tuple[Literal["matching", "vault_signals"], bool]] = []
        if new_matching != self.consent_matching:
            changes.append(("matching", new_matching))
        if new_vault != self.consent_vault_signals:
            changes.append(("vault_signals", new_vault))
        if not changes:
            return
        self.consent_matching, self.consent_vault_signals, self.updated_at = new_matching, new_vault, now
        for consent, value in changes:
            self.record_event(
                ConsentChanged(
                    event_id=UuidGenerator().new(),
                    occurred_at=now,
                    account_id=self.id,
                    consent=consent,
                    value=value,
                )
            )

    def sync_email(self, email: str, now: datetime) -> bool:
        """Adopt the e-mail from the token if it changed."""
        email = email.strip().lower()
        if email == self.email:
            return False
        self.email, self.updated_at = email, now
        return True

    def ensure_active(self) -> None:
        """Raise ``AccountDeleted`` for deleted accounts."""
        if self.deleted_at is not None:
            raise AccountDeleted()
