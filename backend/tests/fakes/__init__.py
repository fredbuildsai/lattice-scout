"""In-memory test doubles. Runtime-selectable offline adapters are re-exported here."""

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.llm.fake_llm import FakeLLM
from scout.shared import FixedClock, SequentialIdGenerator
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.recorders import InMemoryAnalytics, InMemoryUsageMeter
from tests.fakes.uow import InMemoryUnitOfWork

__all__ = [
    "FakeLLM",
    "FixedClock",
    "FixtureLiterature",
    "InMemoryAnalytics",
    "InMemoryJobQueue",
    "InMemoryUnitOfWork",
    "InMemoryUsageMeter",
    "SequentialIdGenerator",
]
