"""In-memory unit of work used by use-case tests."""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self


class InMemoryUnitOfWork:
    """Transactional dict store: changes become visible to later UoWs only after ``commit``."""

    def __init__(self, store: dict[str, Any] | None = None) -> None:
        self.store: dict[str, Any] = store if store is not None else {}
        self._pending: dict[str, Any] = {}
        self.committed = False

    async def __aenter__(self) -> Self:
        self._pending = dict(self.store)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if not self.committed:
            await self.rollback()

    def put(self, key: str, value: Any) -> None:
        """Stage a write."""
        self._pending[key] = value

    def get(self, key: str) -> Any:
        """Read through the staged state."""
        return self._pending.get(key)

    async def commit(self) -> None:
        self.store.clear()
        self.store.update(self._pending)
        self.committed = True

    async def rollback(self) -> None:
        self._pending = dict(self.store)
