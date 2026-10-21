"""File storage port (shared: the vault uses it now, account deletion in M6)."""

from __future__ import annotations

import re
from typing import Protocol

from scout.shared import InfrastructureError, ValidationFailed

_KEY = re.compile(r"^[a-z0-9][a-z0-9/_-]*$")


class StorageObjectMissing(InfrastructureError):
    """The requested object does not exist."""

    code = "storage_object_missing"
    http_status = 404
    title = "Stored file not found"


def validate_key(key: str) -> str:
    """Return ``key`` if it is a safe storage key.

    Raises:
        ValidationFailed: for empty keys, ``..`` segments, a leading ``/`` or characters outside ``[a-z0-9/_-]``.
    """
    if not _KEY.match(key) or ".." in key or "//" in key or key.endswith("/"):
        raise ValidationFailed("Invalid storage key")
    return key


class FileStoragePort(Protocol):
    """Private blob storage. Keys look like ``vault/{owner_id}/{document_id}``; they never contain filenames."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` under ``key`` (overwrites)."""
        ...

    async def get(self, key: str) -> bytes:
        """Return the bytes.

        Raises:
            StorageObjectMissing: if the key does not exist.
        """
        ...

    async def delete(self, key: str) -> None:
        """Delete ``key``; a missing key is not an error."""
        ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with ``prefix + '/'``; return how many were removed."""
        ...

    async def exists(self, key: str) -> bool:
        """Whether the key exists."""
        ...
