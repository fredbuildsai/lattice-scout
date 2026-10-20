"""Local-disk storage for development and tests (``var/storage/``)."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

from scout.ports.storage import StorageObjectMissing, validate_key


class LocalFileStorage:
    """``FileStoragePort`` on a directory: atomic writes, mode 0600."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self._root / validate_key(key)).resolve()
        if self._root not in p.parents:
            raise StorageObjectMissing("Invalid storage key")
        return p

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write atomically (temp file then rename)."""
        await asyncio.to_thread(self._put, self._path(key), data)

    @staticmethod
    def _put(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def get(self, key: str) -> bytes:
        """Read the object."""
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise StorageObjectMissing("Stored file not found") from None

    async def delete(self, key: str) -> None:
        """Remove the object if present."""
        await asyncio.to_thread(self._path(key).unlink, True)

    async def delete_prefix(self, prefix: str) -> int:
        """Remove every object under ``prefix``."""
        directory = self._path(prefix)
        if not directory.is_dir():
            return 0
        count = sum(1 for p in directory.rglob("*") if p.is_file())
        await asyncio.to_thread(shutil.rmtree, directory, True)
        return count

    async def exists(self, key: str) -> bool:
        """Whether the object exists."""
        return self._path(key).is_file()
