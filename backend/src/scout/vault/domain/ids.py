"""Vault identifiers."""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid5

DocumentId = NewType("DocumentId", UUID)
ChunkId = NewType("ChunkId", UUID)

CHUNK_NS = UUID("6f1c2b8e-0d4a-5b7e-9c31-a2f4e8d0b915")
CHUNKER_VERSION = 1


def chunk_id(document_id: UUID, ord_: int) -> ChunkId:
    """Deterministic chunk id: the same document and position always give the same id (job re-runs are safe)."""
    return ChunkId(uuid5(CHUNK_NS, f"{document_id}:{CHUNKER_VERSION}:{ord_}"))


def storage_key(owner_id: UUID, document_id: UUID) -> str:
    """Private bucket key. Never contains the filename."""
    return f"vault/{owner_id}/{document_id}"
