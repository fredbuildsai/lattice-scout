"""In-memory test doubles. Runtime-selectable offline adapters are re-exported here as they are added."""

from scout.shared import FixedClock, SequentialIdGenerator
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.uow import InMemoryUnitOfWork

__all__ = ["FixedClock", "InMemoryJobQueue", "InMemoryUnitOfWork", "SequentialIdGenerator"]
