"""Composition root: the only module that wires adapters into services.

Optional or heavy adapters (openai, agents, llmrouter_free) are imported inside their branch so that the
default install, tests and CI never import them.
"""

from __future__ import annotations

from dataclasses import dataclass

from scout.adapters.persistence.db import Database
from scout.settings import Settings
from scout.shared import Clock, IdGenerator, SystemClock, UuidGenerator


@dataclass(frozen=True, slots=True)
class Container:
    """Everything use cases need. Override fields in tests with ``dataclasses.replace``."""

    settings: Settings
    clock: Clock
    ids: IdGenerator
    db: Database


def build_container(
    settings: Settings, *, clock: Clock | None = None, ids: IdGenerator | None = None
) -> Container:
    """Create the container. Opens no connection and starts no background task."""
    return Container(
        settings=settings,
        clock=clock or SystemClock(),
        ids=ids or UuidGenerator(),
        db=Database(settings.database_url),
    )


async def start_container(container: Container) -> None:
    """Start background writers (none yet)."""


async def close_container(container: Container) -> None:
    """Flush buffers, then dispose the engine."""
    await container.db.dispose()
