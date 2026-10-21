"""In-memory test doubles. Runtime-selectable offline adapters are re-exported here."""

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.adapters.llm.fake_llm import FakeLLM
from scout.shared import FixedClock, SequentialIdGenerator
from tests.fakes.extraction import FakeTextExtractor
from tests.fakes.jobs import InMemoryJobQueue
from tests.fakes.recorders import InMemoryAnalytics, InMemoryUsageMeter
from tests.fakes.storage import InMemoryFileStorage
from tests.fakes.uow import InMemoryUnitOfWork
from tests.fakes.vault import InMemoryVaultUoW, VaultStore

__all__ = [
    "FakeLLM",
    "FakeTextExtractor",
    "FixedClock",
    "FixtureLiterature",
    "InMemoryAnalytics",
    "InMemoryFileStorage",
    "InMemoryJobQueue",
    "InMemoryUnitOfWork",
    "InMemoryUsageMeter",
    "InMemoryVaultUoW",
    "SequentialIdGenerator",
    "VaultStore",
]
