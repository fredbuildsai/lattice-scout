"""Time source. Always timezone-aware UTC."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Port for the current time."""

    def now(self) -> datetime:
        """Return the current time as an aware UTC datetime."""
        ...


class SystemClock:
    """Real clock."""

    def now(self) -> datetime:
        """Return ``datetime.now(UTC)``."""
        return datetime.now(UTC)


class FixedClock:
    """Controllable clock for tests."""

    def __init__(self, at: datetime) -> None:
        self._at = self._check(at)

    @staticmethod
    def _check(at: datetime) -> datetime:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        return at.astimezone(UTC)

    def now(self) -> datetime:
        """Return the frozen time."""
        return self._at

    def set(self, at: datetime) -> None:
        """Jump to ``at`` (must be aware)."""
        self._at = self._check(at)

    def advance(self, delta: timedelta) -> None:
        """Move forward by ``delta`` (must not be negative)."""
        if delta < timedelta(0):
            raise ValueError("FixedClock cannot go backwards")
        self._at = self._at + delta
