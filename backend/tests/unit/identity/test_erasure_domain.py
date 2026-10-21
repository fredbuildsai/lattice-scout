"""M6 slice A, identity domain: ``subject_hash``, ``ErasureSubject``, ``Account.request_deletion``
(plan M6 task 2; docs/design/M6-deletion.md sections 3 "subject_hash", 3 "Flow", 7 step 1; decisions 90 and 92).

Assumed API (design note, no production code exists yet):
* ``scout.identity.domain.erasure.subject_hash(key: str, account_id: UUID) -> str`` is
  ``hmac_sha256(key, str(account_id)).hexdigest()`` (stdlib ``hmac``).
* ``ErasureSubject``: frozen Pydantic model with ``account_id``, ``subject_hash``, ``researcher_id: UUID | None``
  and the ``frozenset[UUID]`` fields ``document_ids``, ``run_ids``, ``opportunity_ids``, ``session_ids``,
  ``mutual_match_ids`` (all defaulting to empty); ``needles() -> list[str]`` returns every id in string form
  (``mutual_match_ids`` included: the scan test decides which needles to use).
* ``Account.request_deletion(now) -> bool`` sets ``deleted_at``, turns both consents off, records
  ``AccountDeletionRequested`` (in ``scout.identity.domain.account``) and returns True; a second call is a no-op
  returning False.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from scout.identity.domain.erasure import ErasureSubject, subject_hash

from scout.identity.domain.account import Account, AccountDeleted, ConsentRequiresMatching
from scout.shared import AccountId

NOW = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
A = AccountId(UUID(int=5))
KEY = "test-audit-key"


def _acct() -> Account:
    return Account.create(A, "ada@example.edu", "bio-based-materials", NOW)


# ------------------------------------------------------------------ subject_hash


def test_subject_hash_is_hmac_sha256_of_the_id_string() -> None:
    expected = hmac.new(KEY.encode(), str(A).encode(), hashlib.sha256).hexdigest()
    assert subject_hash(KEY, A) == expected


def test_subject_hash_is_stable_keyed_and_does_not_contain_the_id() -> None:
    h = subject_hash(KEY, A)
    assert h == subject_hash(KEY, A)
    assert h != subject_hash("another-key", A)
    assert h != subject_hash(KEY, UUID(int=6))
    assert len(h) == 64 and str(A) not in h and A.hex not in h


@given(st.uuids(), st.uuids())
def test_subject_hash_is_injective_on_distinct_ids(a: UUID, b: UUID) -> None:
    assert (subject_hash(KEY, a) == subject_hash(KEY, b)) == (a == b)


# ------------------------------------------------------------------ ErasureSubject


def _full_subject() -> ErasureSubject:
    return ErasureSubject(
        account_id=A,
        subject_hash=subject_hash(KEY, A),
        researcher_id=UUID(int=10),
        document_ids=frozenset({UUID(int=11), UUID(int=12)}),
        run_ids=frozenset({UUID(int=13)}),
        opportunity_ids=frozenset({UUID(int=14)}),
        session_ids=frozenset({UUID(int=15)}),
        mutual_match_ids=frozenset({UUID(int=16)}),
    )


def test_subject_with_only_the_account_id_has_empty_collections() -> None:
    s = ErasureSubject(account_id=A, subject_hash=subject_hash(KEY, A))
    assert s.researcher_id is None
    for name in ("document_ids", "run_ids", "opportunity_ids", "session_ids", "mutual_match_ids"):
        assert getattr(s, name) == frozenset()
    assert s.needles() == [str(A)]


def test_subject_is_frozen() -> None:
    s = _full_subject()
    with pytest.raises(ValidationError):
        s.account_id = UUID(int=99)  # type: ignore[misc]


def test_needles_are_the_string_forms_of_every_purge_id_without_duplicates() -> None:
    s = _full_subject()
    needles = s.needles()
    assert all(isinstance(n, str) for n in needles)
    assert sorted(needles) == sorted(str(UUID(int=i)) for i in (5, 10, 11, 12, 13, 14, 15, 16))
    assert len(needles) == len(set(needles))
    assert s.subject_hash not in needles  # the hash is what stays; it is not a purge id


@given(
    st.sets(st.uuids(), max_size=5),
    st.sets(st.uuids(), max_size=5),
    st.sets(st.uuids(), max_size=5),
)
def test_needles_cover_exactly_the_union_of_ids(
    docs: set[UUID], runs: set[UUID], sessions: set[UUID]
) -> None:
    s = ErasureSubject(
        account_id=A,
        subject_hash="h",
        document_ids=frozenset(docs),
        run_ids=frozenset(runs),
        session_ids=frozenset(sessions),
    )
    assert set(s.needles()) == {str(x) for x in {A, *docs, *runs, *sessions}}


# ------------------------------------------------------------------ Account.request_deletion


def test_request_deletion_marks_the_account_and_turns_consents_off() -> None:
    a = _acct()
    a.set_consent(matching=True, vault_signals=True, now=NOW)
    a.pull_events()
    later = NOW + timedelta(hours=1)
    assert a.request_deletion(later) is True
    assert a.deleted_at == later
    assert not a.consent_matching and not a.consent_vault_signals
    assert [type(e).__name__ for e in a.pull_events()] == ["AccountDeletionRequested"]


def test_request_deletion_makes_ensure_active_raise() -> None:
    a = _acct()
    a.ensure_active()
    a.request_deletion(NOW)
    with pytest.raises(AccountDeleted) as info:
        a.ensure_active()
    assert info.value.code == "account_deleted" and info.value.http_status == 403


def test_request_deletion_is_idempotent_and_keeps_the_first_timestamp() -> None:
    a = _acct()
    assert a.request_deletion(NOW) is True
    a.pull_events()
    assert a.request_deletion(NOW + timedelta(days=3)) is False
    assert a.deleted_at == NOW
    assert a.pull_events() == []


def test_request_deletion_event_carries_the_account_id() -> None:
    a = _acct()
    a.pull_events()
    a.request_deletion(NOW)
    (event,) = a.pull_events()
    assert event.account_id == A  # type: ignore[attr-defined]


@given(
    st.lists(
        st.tuples(st.sampled_from([None, True, False]), st.sampled_from([None, True, False])), max_size=10
    )
)
def test_consents_are_off_after_deletion_for_any_history(
    patches: list[tuple[bool | None, bool | None]],
) -> None:
    a = _acct()
    for m, v in patches:
        with contextlib.suppress(ConsentRequiresMatching):
            a.set_consent(matching=m, vault_signals=v, now=NOW)
    a.request_deletion(NOW)
    assert a.deleted_at is not None
    assert not a.consent_matching and not a.consent_vault_signals
