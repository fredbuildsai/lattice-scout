"""Domain events and the aggregate base class that records them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class DomainEvent(BaseModel):
    """Immutable fact that happened in a bounded context."""

    model_config = ConfigDict(frozen=True)

    name: ClassVar[str] = "shared.event"
    event_id: UUID
    occurred_at: AwareDatetime


@dataclass
class AggregateRoot:
    """Base for aggregates: collects events until a use case pulls them."""

    _events: list[DomainEvent] = field(default_factory=list, init=False, repr=False, compare=False)

    def record_event(self, event: DomainEvent) -> None:
        """Remember ``event`` for later dispatch."""
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        """Return the recorded events in order and clear them."""
        events, self._events = self._events, []
        return events
