"""``DeleteDocumentJob``: the ``vault.delete`` job removes the stored file, then the row."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.ports.storage import FileStoragePort
from scout.shared import Clock
from scout.vault.domain.enums import DocStatus
from scout.vault.ports.repos import VaultUnitOfWork

log = structlog.get_logger()


class DeleteDocumentJob:
    """The ``deleting`` row is the outbox entry: it is removed only after the stored file is gone."""

    def __init__(self, uow: Callable[[], VaultUnitOfWork], storage: FileStoragePort, clock: Clock) -> None:
        self._uow, self._storage, self._clock = uow, storage, clock

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> dict[str, JsonValue]:
        """Delete the object of ``payload['document_id']``; a storage failure re-raises so the job retries."""
        document_id = UUID(str(payload["document_id"]))
        async with self._uow() as uow:
            doc = await uow.documents.get_for_update(document_id)
        if doc is None:
            return {"skipped": "gone"}
        if doc.status is not DocStatus.DELETING:
            return {"skipped": "not_deleting"}
        try:
            await self._storage.delete(doc.storage_key)
        except Exception:
            log.error("vault.storage_delete_failed", document_id=str(document_id), last_attempt=last_attempt)
            raise
        async with self._uow() as uow:
            await uow.documents.hard_delete(document_id)
            await uow.commit()
        log.info("vault.deleted", document_id=str(document_id))
        return {"deleted": True}
