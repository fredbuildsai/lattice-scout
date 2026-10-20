"""M1 task 2: Account aggregate invariants and IdentityService (against fakes). ARCHITECTURE section 4.1, decisions 27-29."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.fakes.identity import IdentityStore, InMemoryIdentityUoW

from scout.identity.application.service import EmailRequired, IdentityService
from scout.identity.domain.account import Account, ConsentChanged, ConsentRequiresMatching
from scout.shared import AccountId, FixedClock, NotFound, SequentialIdGenerator, ValidationFailed

NOW = datetime(2026, 1, 1, tzinfo=UTC)
A = AccountId(UUID(int=5))


def _acct() -> Account:
    return Account.create(A, " Ada@Example.edu ", "bio-based-materials", NOW)


def test_create_defaults_and_event() -> None:
    a = _acct()
    assert a.email == "ada@example.edu" and not a.consent_matching and not a.consent_vault_signals
    assert [type(e).__name__ for e in a.pull_events()] == ["AccountCreated"]


def test_vault_signals_require_matching() -> None:
    a = _acct()
    with pytest.raises(ConsentRequiresMatching):
        a.set_consent(matching=None, vault_signals=True, now=NOW)
    a.set_consent(matching=True, vault_signals=True, now=NOW)
    a.set_consent(matching=False, vault_signals=None, now=NOW)
    assert not a.consent_vault_signals


def test_no_change_no_event_no_timestamp_bump() -> None:
    a = _acct()
    a.pull_events()
    a.set_consent(matching=False, vault_signals=None, now=datetime(2027, 1, 1, tzinfo=UTC))
    assert a.pull_events() == [] and a.updated_at == NOW


@given(
    st.lists(
        st.tuples(st.sampled_from([None, True, False]), st.sampled_from([None, True, False])), max_size=25
    )
)
def test_consent_invariant_holds_for_any_sequence(patches: list[tuple[bool | None, bool | None]]) -> None:
    a = _acct()
    a.pull_events()
    changes = 0
    for m, v in patches:
        before = (a.consent_matching, a.consent_vault_signals)
        try:
            a.set_consent(matching=m, vault_signals=v, now=NOW)
        except ConsentRequiresMatching:
            assert (a.consent_matching, a.consent_vault_signals) == before
        assert not a.consent_vault_signals or a.consent_matching
        changes += sum(isinstance(e, ConsentChanged) for e in a.pull_events())
    assert changes >= 0


def _svc() -> tuple[IdentityService, IdentityStore, FixedClock]:
    store, clock = IdentityStore(), FixedClock(NOW)
    return (
        IdentityService(lambda: InMemoryIdentityUoW(store), clock, SequentialIdGenerator(100), "bio"),
        store,
        clock,
    )


async def test_ensure_account_creates_once_and_audits_once() -> None:
    svc, store, _ = _svc()
    a1 = await svc.ensure_account(UUID(int=1), "x@example.edu")
    a2 = await svc.ensure_account(UUID(int=1), "x@example.edu")
    assert a1.id == a2.id and len(store.accounts) == 1 and store.audit.actions() == ["account.created"]


async def test_ensure_account_requires_email_and_syncs_changes() -> None:
    svc, store, _ = _svc()
    with pytest.raises(EmailRequired):
        await svc.ensure_account(UUID(int=1), None)
    await svc.ensure_account(UUID(int=1), "x@example.edu")
    a = await svc.ensure_account(UUID(int=1), "y@example.edu")
    assert a.email == "y@example.edu" and store.accounts[AccountId(UUID(int=1))].email == "y@example.edu"


async def test_update_consent_audits_each_change() -> None:
    svc, store, _ = _svc()
    await svc.ensure_account(UUID(int=1), "x@example.edu")
    a = await svc.update_consent(AccountId(UUID(int=1)), True, True)
    assert a.consent_matching and a.consent_vault_signals
    assert store.audit.actions() == ["account.created", "consent.changed", "consent.changed"]
    with pytest.raises(ValidationFailed):
        await svc.update_consent(AccountId(UUID(int=1)), None, None)
    with pytest.raises(NotFound):
        await svc.update_consent(AccountId(UUID(int=99)), True, None)
