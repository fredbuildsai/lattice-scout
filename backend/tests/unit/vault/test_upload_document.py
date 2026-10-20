"""M2 vault, UploadDocument use case (plan M2 test-first; design M2-vault-ingestion.md §5 "UploadDocument", §10 invariants 5 and 9).

Covers: size/type/zip checks, storage-before-insert ordering and orphan cleanup, duplicate sha256, vault limit,
enqueue contract and tolerance, audit row, retention resolution, owner scoping of the duplicate rule.
"""

from __future__ import annotations

import pytest
from tests.unit.vault._env import OTHER, OWNER, VaultEnv, docx_bytes, make_pages, pdf_bytes

from scout.ports.jobs import JobStatus
from scout.shared import Conflict, InfrastructureError
from scout.vault.domain.enums import DocKind, DocStatus, DocumentMime, Retention, RetentionPolicy
from scout.vault.domain.upload import FileRejected, FileTooLarge, UnsupportedFileType
from scout.vault.ports.repos import DuplicateDocument

PDF = pdf_bytes(make_pages(2))


async def test_upload_stores_object_then_row_then_enqueues() -> None:
    env = VaultEnv()
    res = await env.upload_uc().upload(OWNER, "My Proposal.pdf", PDF, True, DocKind.PROPOSAL, None)
    doc = res.document
    assert res.created is True
    assert doc.status is DocStatus.UPLOADED and doc.mime is DocumentMime.PDF
    assert doc.owner_id == OWNER and doc.size_bytes == len(PDF) and doc.kind is DocKind.PROPOSAL
    assert doc.storage_key == f"vault/{OWNER}/{doc.id}" and "Proposal" not in doc.storage_key
    assert env.storage.objects == {doc.storage_key: PDF}
    assert env.storage.content_types[doc.storage_key] == DocumentMime.PDF.value
    assert env.store.documents[doc.id].sha256 == doc.sha256 and len(doc.sha256) == 64


async def test_upload_enqueues_ingest_job_with_contract() -> None:
    env = VaultEnv()
    session = env.ids.new()
    res = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None, session)
    (job,) = env.jobs.jobs.values()
    assert job.kind == "vault.ingest" and job.status is JobStatus.QUEUED and job.max_attempts == 4
    assert job.payload["document_id"] == str(res.document.id)
    assert job.payload["account_id"] == str(OWNER)
    assert job.payload["feature"] == "vault.ingest"
    assert job.payload.get("session_id") == str(session)
    assert env.jobs.dedupe[job.id] == ("vault.ingest", str(res.document.id))


async def test_upload_without_session_has_no_session_in_payload() -> None:
    env = VaultEnv()
    await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    (job,) = env.jobs.jobs.values()
    assert job.payload.get("session_id") is None


async def test_upload_writes_audit_row_in_same_commit() -> None:
    env = VaultEnv()
    doc = (await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)).document
    (row,) = env.audit_rows("document.uploaded")
    assert row.actor_id == OWNER
    assert row.meta["document_id"] == str(doc.id)
    assert row.meta["mime"] == DocumentMime.PDF.value
    assert row.meta["size_bytes"] == len(PDF) and row.meta["retention"] == doc.retention.value


async def test_upload_cleans_filename_and_never_puts_it_in_key() -> None:
    env = VaultEnv()
    doc = (await env.upload_uc().upload(OWNER, "../../etc/evil\x00name.pdf", PDF, True, None)).document
    assert doc.filename == "evilname.pdf"
    assert "evil" not in doc.storage_key


async def test_docx_is_sniffed_from_bytes_not_from_the_name() -> None:
    env = VaultEnv()
    doc = (await env.upload_uc().upload(OWNER, "really-a-docx.pdf", docx_bytes(), True, None)).document
    assert doc.mime is DocumentMime.DOCX
    assert env.storage.content_types[doc.storage_key] == DocumentMime.DOCX.value


@pytest.mark.parametrize("data", [b"hello world, not a document", b"PK\x03\x04truncated", b"\x00" * 64])
async def test_unsupported_type_is_rejected_without_side_effects(data: bytes) -> None:
    env = VaultEnv()
    with pytest.raises(UnsupportedFileType):
        await env.upload_uc().upload(OWNER, "report.pdf", data, True, None)
    assert not env.storage.objects and not env.store.documents and not env.jobs.jobs


async def test_zip_bomb_is_rejected_without_side_effects() -> None:
    env = VaultEnv()
    with pytest.raises(FileRejected):
        await env.upload_uc().upload(OWNER, "bomb.docx", docx_bytes(bomb=True), True, None)
    assert not env.storage.objects and not env.store.documents and not env.jobs.jobs


async def test_too_large_and_empty_are_413_class_errors() -> None:
    env = VaultEnv()
    uc = env.upload_uc(max_bytes=len(PDF))
    with pytest.raises(FileTooLarge):
        await uc.upload(OWNER, "a.pdf", PDF + b"x", True, None)
    with pytest.raises(FileTooLarge):
        await uc.upload(OWNER, "a.pdf", b"", True, None)
    assert FileTooLarge.http_status == 413 and UnsupportedFileType.http_status == 415
    assert not env.storage.objects and not env.jobs.jobs
    assert (await uc.upload(OWNER, "a.pdf", PDF, True, None)).created is True  # exactly max_bytes is fine


async def test_size_is_checked_before_the_type() -> None:
    env = VaultEnv()
    with pytest.raises(FileTooLarge):
        await env.upload_uc(max_bytes=10).upload(OWNER, "a.pdf", b"not a pdf at all" * 3, True, None)


async def test_duplicate_returns_existing_without_new_object_or_job() -> None:
    env = VaultEnv()
    first = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    again = await env.upload_uc().upload(OWNER, "other-name.pdf", PDF, False, DocKind.OTHER)
    assert again.created is False and again.document.id == first.document.id
    assert len(env.jobs.jobs) == 1 and len(env.storage.objects) == 1 and len(env.store.documents) == 1
    assert len(env.audit_rows("document.uploaded")) == 1


async def test_duplicate_rule_is_per_owner() -> None:
    env = VaultEnv()
    a = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    b = await env.upload_uc().upload(OTHER, "a.pdf", PDF, True, None)
    assert b.created is True and b.document.id != a.document.id
    assert len(env.storage.objects) == 2 and len(env.jobs.jobs) == 2


@pytest.mark.parametrize("terminal", [DocStatus.FAILED, DocStatus.REJECTED])
async def test_failed_or_rejected_documents_do_not_block_a_new_upload(terminal: DocStatus) -> None:
    env = VaultEnv()
    first = (await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)).document
    env.store.documents[first.id].status = terminal
    again = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert again.created is True
    assert again.document.id != first.id


async def test_vault_limit_reached() -> None:
    env = VaultEnv()
    uc = env.upload_uc(max_documents=2)
    await uc.upload(OWNER, "1.pdf", pdf_bytes(make_pages(1, seed=1)), True, None)
    await uc.upload(OWNER, "2.pdf", pdf_bytes(make_pages(1, seed=2)), True, None)
    with pytest.raises(Conflict) as exc:
        await uc.upload(OWNER, "3.pdf", pdf_bytes(make_pages(1, seed=3)), True, None)
    assert "vault_limit_reached" in {exc.value.code, exc.value.detail}
    assert len(env.storage.objects) == 2 and len(env.jobs.jobs) == 2 and len(env.store.documents) == 2
    # another owner has their own quota
    assert (await uc.upload(OTHER, "3.pdf", pdf_bytes(make_pages(1, seed=3)), True, None)).created is True


async def test_duplicate_is_answered_before_the_limit_check() -> None:
    env = VaultEnv()
    uc = env.upload_uc(max_documents=1)
    first = await uc.upload(OWNER, "1.pdf", PDF, True, None)
    again = await uc.upload(OWNER, "1.pdf", PDF, True, None)
    assert again.created is False and again.document.id == first.document.id


async def test_documents_being_deleted_do_not_count_against_the_limit() -> None:
    env = VaultEnv()
    uc = env.upload_uc(max_documents=1)
    first = await uc.upload(OWNER, "1.pdf", pdf_bytes(make_pages(1, seed=1)), True, None)
    await env.service().delete(OWNER, first.document.id)
    assert (await uc.upload(OWNER, "2.pdf", pdf_bytes(make_pages(1, seed=2)), True, None)).created is True


async def test_storage_put_failure_leaves_no_row_and_no_job() -> None:
    env = VaultEnv()
    env.storage.fail_next("put")
    with pytest.raises(InfrastructureError):
        await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert not env.store.documents and not env.jobs.jobs and not env.storage.objects


async def test_insert_failure_removes_the_stored_object_and_reraises() -> None:
    env = VaultEnv()
    env.faults.fail_add = RuntimeError("db down")
    with pytest.raises(RuntimeError, match="db down"):
        await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert not env.storage.objects and not env.store.documents and not env.jobs.jobs


async def test_insert_failure_with_failing_cleanup_still_reraises_the_original_error() -> None:
    env = VaultEnv()
    env.faults.fail_add = RuntimeError("db down")
    env.storage.fail_next("delete")
    with pytest.raises(RuntimeError, match="db down"):
        await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert not env.store.documents and not env.jobs.jobs


async def test_commit_failure_removes_the_stored_object() -> None:
    env = VaultEnv()
    env.faults.arm(1)  # the first commit after this point (the insert) fails
    with pytest.raises(RuntimeError):
        await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert not env.storage.objects and not env.store.documents and not env.jobs.jobs


async def test_lost_unique_index_race_returns_the_winner_and_removes_own_object() -> None:
    env = VaultEnv()
    winner = (await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)).document
    env.faults.blind_once = True  # the pre-check misses the winner; the insert then hits the unique index
    res = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert res.created is False and res.document.id == winner.id
    assert set(env.storage.objects) == {winner.storage_key}
    assert len(env.jobs.jobs) == 1 and len(env.store.documents) == 1
    assert issubclass(DuplicateDocument, Conflict)


async def test_enqueue_failure_does_not_fail_the_upload() -> None:
    env = VaultEnv()
    env.jobs.fail_enqueue = True
    res = await env.upload_uc().upload(OWNER, "a.pdf", PDF, True, None)
    assert res.created is True and res.document.status is DocStatus.UPLOADED
    assert res.document.id in env.store.documents and res.document.storage_key in env.storage.objects
    assert not env.jobs.jobs


@pytest.mark.parametrize(
    ("policy", "keep", "expected"),
    [
        (RetentionPolicy.KEEP, None, Retention.KEEP),
        (RetentionPolicy.KEEP, False, Retention.KEEP),
        (RetentionPolicy.KEEP, True, Retention.KEEP),
        (RetentionPolicy.DISCARD, None, Retention.DISCARD),
        (RetentionPolicy.DISCARD, False, Retention.DISCARD),
        (RetentionPolicy.DISCARD, True, Retention.DISCARD),
        (RetentionPolicy.USER_CHOICE, None, Retention.DISCARD),
        (RetentionPolicy.USER_CHOICE, False, Retention.DISCARD),
        (RetentionPolicy.USER_CHOICE, True, Retention.KEEP),
    ],
)
async def test_retention_is_resolved_at_upload(
    policy: RetentionPolicy, keep: bool | None, expected: Retention
) -> None:
    env = VaultEnv()
    res = await env.upload_uc(policy).upload(OWNER, "a.pdf", PDF, keep, None)
    assert res.document.retention is expected
    assert env.store.documents[res.document.id].retention is expected
    assert env.audit_rows("document.uploaded")[0].meta["retention"] == expected.value
