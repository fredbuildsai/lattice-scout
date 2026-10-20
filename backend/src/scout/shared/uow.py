"""Unit of work protocol: one transaction per use case."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, Self


class UnitOfWork(Protocol):
    """Transaction boundary. Leaving the block without ``commit`` rolls back."""

    async def __aenter__(self) -> Self:
        """Open the transaction."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Roll back anything not committed."""
        ...

    async def commit(self) -> None:
        """Make the changes durable."""
        ...

    async def rollback(self) -> None:
        """Discard the changes."""
        ...


type UnitOfWorkFactory = Callable[[], UnitOfWork]
