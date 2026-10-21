"""In-memory feedback repository and target check (staged copies; ``commit`` publishes)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import TracebackType
from typing import Self
from uuid import UUID

from scout.feedback.domain.feedback import Feedback, TargetType
from scout.shared import AccountId

Key = tuple[UUID, TargetType, UUID]


@dataclass
class FeedbackStore:
    """Committed state: ratings and the targets each author may rate."""

    ratings: dict[Key, Feedback] = field(default_factory=dict)
    rateable: set[Key] = field(default_factory=set)


class InMemoryFeedbackRepo:
    def __init__(self, data: dict[Key, Feedback]) -> None:
        self.data = data

    async def upsert(self, feedback: Feedback) -> Feedback:
        key = (feedback.author_id, feedback.target_type, feedback.target_id)
        old = self.data.get(key)
        stored = replace(feedback, created_at=old.created_at) if old else feedback
        self.data[key] = stored
        return stored

    async def get(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> Feedback | None:
        return self.data.get((author_id, target_type, target_id))


class InMemoryTargetCheck:
    def __init__(self, allowed: set[Key]) -> None:
        self.allowed = allowed

    async def rateable(self, author_id: AccountId, target_type: TargetType, target_id: UUID) -> bool:
        return (author_id, target_type, target_id) in self.allowed


class InMemoryFeedbackUnitOfWork:
    def __init__(self, store: FeedbackStore) -> None:
        self.store = store
        self._staged: dict[Key, Feedback] = {}
        self._feedback = InMemoryFeedbackRepo(self._staged)
        self._targets = InMemoryTargetCheck(store.rateable)

    @property
    def feedback(self) -> InMemoryFeedbackRepo:
        return self._feedback

    @property
    def targets(self) -> InMemoryTargetCheck:
        return self._targets

    async def __aenter__(self) -> Self:
        self._staged.clear()
        self._staged.update(self.store.ratings)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def rollback(self) -> None:
        self._staged.clear()
        self._staged.update(self.store.ratings)

    async def commit(self) -> None:
        self.store.ratings.clear()
        self.store.ratings.update(self._staged)
