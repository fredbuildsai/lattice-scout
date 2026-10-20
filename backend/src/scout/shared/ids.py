"""Identifier helpers shared by every bounded context."""

from __future__ import annotations

from typing import NewType, Protocol
from uuid import UUID, uuid4

from scout.shared.errors import ValidationFailed

EntityId = UUID
"""Identifier of any entity. Contexts define ``NewType`` wrappers (``AccountId``...) for type safety."""


AccountId = NewType("AccountId", UUID)
"""Identifier of an account (= the auth user id); shared because every context refers to owners."""


class IdGenerator(Protocol):
    """Source of new identifiers (injected so tests are deterministic)."""

    def new(self) -> UUID:
        """Return a fresh identifier."""
        ...


class UuidGenerator:
    """Production generator: random UUID version 4."""

    def new(self) -> UUID:
        """Return a random UUID4."""
        return uuid4()


class SequentialIdGenerator:
    """Deterministic generator for tests: ``UUID(int=start)``, ``UUID(int=start+1)``, ..."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def new(self) -> UUID:
        """Return the next UUID in the sequence."""
        value = UUID(int=self._next)
        self._next += 1
        return value


def parse_id(raw: str) -> UUID:
    """Parse a UUID string.

    Raises:
        ValidationFailed: if ``raw`` is not a valid UUID.
    """
    try:
        return UUID(raw)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValidationFailed("Invalid identifier") from exc
