"""In-memory ``FileStoragePort`` with scripted failures."""

from __future__ import annotations

from scout.ports.storage import StorageObjectMissing, validate_key
from scout.shared import InfrastructureError


class InMemoryFileStorage:
    """Dict-backed storage; ``fail_next(op, n)`` makes the next ``n`` calls of ``op`` raise."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self._failures: dict[str, int] = {}

    def fail_next(self, op: str, n: int = 1) -> None:
        """Make the next ``n`` calls of ``op`` (put/get/delete/delete_prefix/exists) raise."""
        self._failures[op] = n

    def _maybe_fail(self, op: str) -> None:
        if self._failures.get(op, 0) > 0:
            self._failures[op] -= 1
            raise InfrastructureError("storage unavailable")

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store."""
        self._maybe_fail("put")
        self.objects[validate_key(key)] = data
        self.content_types[key] = content_type

    async def get(self, key: str) -> bytes:
        """Fetch."""
        self._maybe_fail("get")
        try:
            return self.objects[validate_key(key)]
        except KeyError:
            raise StorageObjectMissing("Stored file not found") from None

    async def delete(self, key: str) -> None:
        """Delete (missing is fine)."""
        self._maybe_fail("delete")
        self.objects.pop(validate_key(key), None)

    async def delete_prefix(self, prefix: str) -> int:
        """Delete the prefix."""
        self._maybe_fail("delete_prefix")
        validate_key(prefix)
        gone = [k for k in self.objects if k.startswith(prefix + "/")]
        for k in gone:
            del self.objects[k]
        return len(gone)

    async def exists(self, key: str) -> bool:
        """Whether present."""
        self._maybe_fail("exists")
        return validate_key(key) in self.objects
