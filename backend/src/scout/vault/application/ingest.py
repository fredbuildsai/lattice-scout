"""``IngestDocument``: the ``vault.ingest`` job (extract, chunk, analyse, apply retention). At-least-once and resumable."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.ports.audit import AuditEntry
from scout.ports.llm import CallContext
from scout.ports.storage import FileStoragePort, StorageObjectMissing
from scout.shared import Clock, UuidGenerator
from scout.vault.application.extract_insights import ExtractInsights
from scout.vault.domain.chunking import chunk_pages
from scout.vault.domain.document import Document
from scout.vault.domain.enums import DocStatus, Retention
from scout.vault.domain.retention import bounded_stored_for_discard, retained_content
from scout.vault.ports.extraction import ExtractionRejected, TextExtractionPort
from scout.vault.ports.repos import VaultUnitOfWork

log = structlog.get_logger()
_IDS = UuidGenerator()
Result = dict[str, JsonValue]


@dataclass
class _Phase:
    """What was running when an exception escaped; becomes the ``failure_code`` after the last attempt."""

    code: str = "extraction_failed"


async def fail_document(
    uow_factory: Callable[[], VaultUnitOfWork], clock: Clock, document_id: UUID, code: str
) -> bool:
    """Mark an in-progress document ``failed``; under ``discard`` redact its chunk text in the same commit.

    Returns True when the document was failed by this call.
    """
    async with uow_factory() as uow:
        doc = await uow.documents.get_for_update(document_id)
        if doc is None or not doc.in_progress:
            return False
        now = clock.now()
        doc.fail(code, now)
        await uow.documents.save(doc)
        if doc.needs_purge:
            await uow.chunks.redact(document_id, {}, now)
        await uow.commit()
        return True


class IngestDocument:
    """Drive a document from ``uploaded`` to ``ready`` (or ``rejected`` / ``failed``), then purge per retention."""

    def __init__(
        self,
        uow: Callable[[], VaultUnitOfWork],
        storage: FileStoragePort,
        extractor: TextExtractionPort,
        insights: ExtractInsights,
        clock: Clock,
    ) -> None:
        self._uow, self._storage, self._extractor, self._insights, self._clock = (
            uow,
            storage,
            extractor,
            insights,
            clock,
        )

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> Result:
        """Run the next steps for ``payload['document_id']`` according to the document's current state."""
        document_id = UUID(str(payload["document_id"]))
        account, session = payload.get("account_id"), payload.get("session_id")
        ctx = CallContext(
            feature="vault.insights", account_id=UUID(str(account)) if account else None,
            session_id=UUID(str(session)) if session else None,
        )  # fmt: skip
        phase = _Phase()
        try:
            return await self._advance(document_id, ctx, phase)
        except Exception:
            if last_attempt:
                await self._give_up(document_id, phase.code)
            raise

    async def _load(self, document_id: UUID) -> Document | None:
        async with self._uow() as uow:
            doc = await uow.documents.get_for_update(document_id)
        return None if doc is None or doc.status is DocStatus.DELETING else doc

    async def _advance(self, document_id: UUID, ctx: CallContext, phase: _Phase) -> Result:
        worked = False
        for _ in range(4):  # extract -> analyse -> purge, plus one look at the final state
            doc = await self._load(document_id)
            if doc is None:
                return {"skipped": "gone"}
            if doc.status in (DocStatus.UPLOADED, DocStatus.EXTRACTING):
                done = await self._extract(doc, phase)
            elif doc.status in (DocStatus.EXTRACTED, DocStatus.ANALYSING):
                done = await self._analyse(doc, ctx, phase)
            elif doc.needs_purge:
                phase.code = "storage_unavailable"
                await self._purge(doc)
                return {"status": doc.status.value, "purged": True}
            else:
                return {"status": doc.status.value} if worked else {"skipped": "done"}
            worked = True
            if done is not None:
                return done
        return {"status": "unknown"}

    # ---- steps ------------------------------------------------------------------------------------------------
    async def _extract(self, doc: Document, phase: _Phase) -> Result | None:
        """Returns a result when ingestion ended here, ``None`` to continue with analysis."""
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(doc.id)
            if cur is None or cur.status is DocStatus.DELETING:
                return {"skipped": "gone"}
            cur.start_extraction(self._clock.now())
            await uow.documents.save(cur)
            await uow.commit()
        phase.code = "storage_unavailable"
        try:
            data = await self._storage.get(doc.storage_key)
        except StorageObjectMissing:
            await fail_document(self._uow, self._clock, doc.id, "original_missing")
            await self._purge(await self._load(doc.id))
            return {"status": "failed"}
        phase.code = "extraction_failed"
        try:
            extracted = await self._extractor.extract_text(data, doc.mime)
        except ExtractionRejected as rejection:
            await self._reject(doc.id, rejection.reject_code)
            await self._purge(await self._load(doc.id))
            return {"status": "rejected"}
        chunks = chunk_pages(doc.id, extracted.pages)
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(doc.id)
            if cur is None or cur.status is not DocStatus.EXTRACTING:
                return {"skipped": "gone"}
            await uow.chunks.replace_all(doc.id, chunks)
            cur.finish_extraction(
                pages=len(extracted.pages), basis=extracted.page_basis, chunks=len(chunks),
                extractor=extracted.extractor, now=self._clock.now(),
            )  # fmt: skip
            await uow.documents.save(cur)
            await uow.commit()
        log.info("vault.extracted", document_id=str(doc.id), pages=len(extracted.pages), chunks=len(chunks))
        return None

    async def _reject(self, document_id: UUID, code: str) -> None:
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(document_id)
            if cur is None or cur.status is not DocStatus.EXTRACTING:
                return
            now = self._clock.now()
            cur.reject(code, now)
            await uow.documents.save(cur)
            if cur.needs_purge:
                await uow.chunks.redact(document_id, {}, now)
            await uow.audit.add(
                AuditEntry(
                    id=_IDS.new(),
                    actor_id=None,
                    action="document.rejected",
                    target=str(document_id),
                    at=now,
                    meta={"document_id": str(document_id), "code": code},
                )  # fmt: skip
            )
            await uow.commit()
        log.info("vault.rejected", document_id=str(document_id), code=code)

    async def _analyse(self, doc: Document, ctx: CallContext, phase: _Phase) -> Result | None:
        phase.code = "insights_unavailable"
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(doc.id)
            if cur is None or cur.status is DocStatus.DELETING:
                return {"skipped": "gone"}
            cur.start_analysis(self._clock.now())
            await uow.documents.save(cur)
            chunks = await uow.chunks.list(doc.id)
            await uow.commit()
        stored = await self._insights.run(cur, chunks, ctx)  # no transaction open during the LLM calls
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(doc.id)
            if cur is None or cur.status is not DocStatus.ANALYSING:
                return {"skipped": "gone"}
            now = self._clock.now()
            if cur.retention is Retention.DISCARD:
                stored = bounded_stored_for_discard(stored)
            await uow.insights.upsert(stored)
            cur.finish_analysis(stored.kind_suggested, stored.kind_reason, now)
            await uow.documents.save(cur)
            if (
                cur.needs_purge
            ):  # discard: text is replaced by verified quotes and summaries in this same commit
                await uow.chunks.redact(doc.id, retained_content(chunks, stored.items), now)
            await uow.commit()
        verified = len(stored.visible())
        log.info(
            "vault.ready", document_id=str(doc.id), verified=verified, unverified=len(stored.items) - verified
        )
        phase.code = "storage_unavailable"
        await self._purge(await self._load(doc.id))
        return {"status": "ready"}

    async def _purge(self, doc: Document | None) -> None:
        """Delete the original bytes of a finished ``discard`` document (idempotent, one audit row)."""
        if doc is None or not doc.needs_purge:
            return
        await self._storage.delete(doc.storage_key)
        async with self._uow() as uow:
            cur = await uow.documents.get_for_update(doc.id)
            if (
                cur is not None and cur.status is not DocStatus.DELETING
            ):  # a delete that won the race handles the file
                now = self._clock.now()
                if cur.mark_original_deleted(now):
                    await uow.documents.save(cur)
                    await uow.audit.add(
                        AuditEntry(
                            id=_IDS.new(),
                            actor_id=None,
                            action="document.original_discarded",
                            target=str(doc.id),
                            at=now,
                            meta={"document_id": str(doc.id), "chunks_redacted": cur.chunk_count or 0},
                        )  # fmt: skip
                    )
            await uow.commit()
        log.info("vault.original_discarded", document_id=str(doc.id))

    async def _give_up(self, document_id: UUID, code: str) -> None:
        """After the last attempt: fail the document and, for ``discard``, still remove the original."""
        if not await fail_document(self._uow, self._clock, document_id, code):
            return
        try:
            await self._purge(await self._load(document_id))
        except Exception:
            log.error("vault.purge_failed", document_id=str(document_id))
