"""In-memory profile repositories and unit of work (staged copies; ``commit`` publishes)."""

from __future__ import annotations

import copy
from collections.abc import Collection
from types import TracebackType
from typing import Self
from uuid import UUID

from scout.profile.domain.researcher import Researcher, VerifiedLevel
from scout.profile.domain.work import ClaimedWork
from scout.shared import AccountId
from tests.fakes.audit import InMemoryAuditLog


class ProfileStore:
    """Committed state."""

    def __init__(self) -> None:
        self.researchers: dict[UUID, Researcher] = {}
        self.works: dict[UUID, ClaimedWork] = {}
        self.audit = InMemoryAuditLog()
        self.commits = 0


class InMemoryResearcherRepo:
    def __init__(self, data: dict[UUID, Researcher]) -> None:
        self.data = data

    async def get_by_account(self, account_id: AccountId) -> Researcher | None:
        return next((copy.deepcopy(r) for r in self.data.values() if r.account_id == account_id), None)

    async def get(self, researcher_id: UUID) -> Researcher | None:
        return copy.deepcopy(self.data.get(researcher_id))

    async def get_for_update(self, researcher_id: UUID) -> Researcher | None:
        return await self.get(researcher_id)

    async def lock_authors(self, author_ids: Collection[str]) -> None:
        return None

    async def find_by_author_ids(self, author_ids: Collection[str]) -> list[Researcher]:
        return [copy.deepcopy(r) for r in self.data.values() if set(r.author_ids) & set(author_ids)]

    async def set_verified_level(self, researcher_id: UUID, level: VerifiedLevel) -> None:
        self.data[researcher_id].verified_level = level

    async def add(self, researcher: Researcher) -> None:
        self.data[researcher.id] = copy.deepcopy(researcher)

    async def save(self, researcher: Researcher) -> None:
        self.data[researcher.id] = copy.deepcopy(researcher)


class InMemoryWorkRepo:
    def __init__(self, data: dict[UUID, ClaimedWork]) -> None:
        self.data = data

    async def list_for(self, researcher_id: UUID) -> list[ClaimedWork]:
        return [copy.deepcopy(w) for w in self.data.values() if w.researcher_id == researcher_id]

    async def get(self, work_id: UUID, researcher_id: UUID) -> ClaimedWork | None:
        w = self.data.get(work_id)
        return copy.deepcopy(w) if w and w.researcher_id == researcher_id else None

    async def upsert_many(self, works: list[ClaimedWork]) -> None:
        for w in works:
            key = next(
                (
                    k
                    for k, x in self.data.items()
                    if x.researcher_id == w.researcher_id and x.openalex_work_id == w.openalex_work_id
                ),
                w.id,
            )
            self.data[key] = (
                copy.deepcopy(w) if key == w.id else copy.deepcopy(w).__class__(**{**w.__dict__, "id": key})
            )

    async def save(self, work: ClaimedWork) -> None:
        self.data[work.id] = copy.deepcopy(work)

    async def delete_unsourced(self, researcher_id: UUID, keep_author_ids: Collection[str]) -> int:
        n = 0
        for k, w in list(self.data.items()):
            if w.researcher_id != researcher_id:
                continue
            w.source_author_ids = frozenset(w.source_author_ids & set(keep_author_ids))
            if not w.source_author_ids:
                del self.data[k]
                n += 1
        return n


class InMemoryProfileUoW:
    def __init__(self, store: ProfileStore) -> None:
        self._store = store

    async def __aenter__(self) -> Self:
        self._r = {k: copy.deepcopy(v) for k, v in self._store.researchers.items()}
        self._w = {k: copy.deepcopy(v) for k, v in self._store.works.items()}
        self._researchers, self._works = InMemoryResearcherRepo(self._r), InMemoryWorkRepo(self._w)
        self._audit = InMemoryAuditLog()
        return self

    @property
    def researchers(self) -> InMemoryResearcherRepo:
        return self._researchers

    @property
    def works(self) -> InMemoryWorkRepo:
        return self._works

    @property
    def audit(self) -> InMemoryAuditLog:
        return self._audit

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

    async def commit(self) -> None:
        self._store.researchers.clear()
        self._store.researchers.update(self._r)
        self._store.works.clear()
        self._store.works.update(self._w)
        self._store.audit.entries.extend(self._audit.entries)
        self._store.commits += 1

    async def rollback(self) -> None:
        return None
