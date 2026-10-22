"""Document state machine."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from scout.shared import AccountId, InvalidStateTransition
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocKind, DocStatus, DocumentMime, PageBasis, Retention

NOW = datetime(2026, 1, 1, tzinfo=UTC)
OWNER = AccountId(UUID(int=1))
S = DocStatus


def make(retention: Retention = Retention.KEEP) -> Document:
    return Document.upload(
        id_=UUID(int=9), owner_id=OWNER, filename="a.pdf", mime=DocumentMime.PDF, size_bytes=10, sha256="0" * 64,
        storage_key="vault/x/y", retention=retention, kind_hint=None, now=NOW,
    )  # fmt: skip


def to_status(d: Document, target: DocStatus) -> Document:
    path = {
        S.UPLOADED: [], S.EXTRACTING: ["start"], S.EXTRACTED: ["start", "extract"],
        S.ANALYSING: ["start", "extract", "analyse"], S.AWAITING_PROVIDER: ["start", "extract", "await"], S.READY: ["start", "extract", "analyse", "ready"],
        S.FAILED: ["fail"], S.REJECTED: ["start", "reject"], S.DELETING: ["delete"],
    }[target]  # fmt: skip
    for step in path:
        if step == "start":
            d.start_extraction(NOW)
        elif step == "extract":
            d.finish_extraction(pages=3, basis=PageBasis.PHYSICAL, chunks=2, extractor="x", now=NOW)
        elif step == "analyse":
            d.start_analysis(NOW)
        elif step == "await":
            d.await_provider(NOW)
        elif step == "ready":
            d.finish_analysis(DocKind.PROPOSAL, None, NOW)
        elif step == "fail":
            d.fail("boom", NOW)
        elif step == "reject":
            d.reject("encrypted", NOW)
        else:
            d.request_deletion(NOW)
    assert d.status is target
    return d


def test_happy_path_and_events() -> None:
    d = to_status(make(), S.READY)
    assert d.ready_at == NOW and d.kind_suggested is DocKind.PROPOSAL and d.effective_kind is DocKind.PROPOSAL
    assert [e.name for e in d.pull_events()] == ["vault.document_uploaded", "vault.document_ready"]


ALLOWED = {
    "start_extraction": {S.UPLOADED, S.EXTRACTING},
    "finish_extraction": {S.EXTRACTING},
    "reject": {S.EXTRACTING},
    "start_analysis": {S.EXTRACTED, S.ANALYSING, S.AWAITING_PROVIDER},
    "await_provider": {S.EXTRACTED, S.ANALYSING, S.AWAITING_PROVIDER},
    "finish_analysis": {S.ANALYSING},
    "fail": {S.UPLOADED, S.EXTRACTING, S.EXTRACTED, S.ANALYSING},
}


def apply(d: Document, action: str) -> None:
    {
        "start_extraction": lambda: d.start_extraction(NOW),
        "finish_extraction": lambda: d.finish_extraction(
            pages=1, basis=PageBasis.NONE, chunks=1, extractor="x", now=NOW
        ),
        "reject": lambda: d.reject("corrupt_file", NOW),
        "start_analysis": lambda: d.start_analysis(NOW),
        "await_provider": lambda: d.await_provider(NOW),
        "finish_analysis": lambda: d.finish_analysis(None, None, NOW),
        "fail": lambda: d.fail("x", NOW),
    }[action]()


@pytest.mark.parametrize("action", sorted(ALLOWED))
@pytest.mark.parametrize("status", list(DocStatus))
def test_transition_table(action: str, status: DocStatus) -> None:
    d = to_status(make(), status)
    if status in ALLOWED[action]:
        apply(d, action)
    else:
        with pytest.raises(InvalidStateTransition):
            apply(d, action)


def test_deleting_is_final_and_idempotent() -> None:
    d = to_status(make(), S.DELETING)
    assert d.request_deletion(NOW) is False
    with pytest.raises(InvalidStateTransition):
        d.set_kind(DocKind.OTHER, NOW)
    assert not d.original_available


@pytest.mark.parametrize("status", [s for s in DocStatus if s is not S.DELETING])
def test_delete_from_any_status_and_set_kind(status: DocStatus) -> None:
    d = to_status(make(), status)
    d.set_kind(DocKind.LAB_NOTES, NOW)
    assert d.effective_kind is DocKind.LAB_NOTES
    assert d.request_deletion(NOW) is True


def test_purge_only_for_discard_and_only_when_finished() -> None:
    keep = to_status(make(Retention.KEEP), S.READY)
    assert not keep.needs_purge and keep.original_available
    with pytest.raises(InvalidStateTransition):
        keep.mark_original_deleted(NOW)
    d = to_status(make(Retention.DISCARD), S.EXTRACTED)
    assert not d.needs_purge
    with pytest.raises(InvalidStateTransition):
        d.mark_original_deleted(NOW)
    for final in (S.READY, S.FAILED, S.REJECTED):
        d = to_status(make(Retention.DISCARD), final)
        assert d.needs_purge
        assert d.mark_original_deleted(NOW) is True
        assert d.mark_original_deleted(NOW) is False  # idempotent, one event
        assert not d.needs_purge and not d.original_available
        assert [e.name for e in d.pull_events()].count("vault.original_discarded") == 1


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from([*sorted(ALLOWED), "delete"]), max_size=12))
def test_random_walks_never_leave_final_states(actions: list[str]) -> None:
    d = make()
    for a in actions:
        before = d.status
        try:
            d.request_deletion(NOW) if a == "delete" else apply(d, a)
        except InvalidStateTransition:
            assert d.status is before
        if before in (S.READY, S.FAILED, S.REJECTED):
            assert d.status in (before, S.DELETING)
        if before is S.DELETING:
            assert d.status is S.DELETING
