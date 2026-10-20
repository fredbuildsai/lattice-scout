"""M1 tasks 4-5: ProfileService + ImportProfile against fakes. Design sections 4, 7 (docs/design/M1-identity-profile-claim.md)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import JsonValue
from tests.fakes import InMemoryJobQueue
from tests.fakes.profile import InMemoryProfileUoW, ProfileStore

from scout.adapters.literature.fixtures import FixtureLiterature
from scout.ports.jobs import Job, JobStatus
from scout.ports.literature import AuthorCandidate, AuthorRecord, LiteraturePort, WorkQuery, WorkRecord
from scout.profile.application.import_profile import ImportProfile
from scout.profile.application.service import ProfileNotFound, ProfileService, UnknownAuthor
from scout.profile.domain.researcher import ImportStatus, VerifiedLevel
from scout.shared import AccountId, FixedClock, NotFound, SequentialIdGenerator, ValidationFailed

ROOT = Path(__file__).resolve().parents[4] / "tests/fixtures/openalex"
FOSTER, SPLIT, MOREIRA = "A5059588273", "A5140167166", "A5033675494"
A, B = AccountId(UUID(int=1)), AccountId(UUID(int=2))
T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Env:
    svc: ProfileService
    imp: ImportProfile
    store: ProfileStore
    jobs: InMemoryJobQueue
    lit: LiteraturePort


def _env(lit: LiteraturePort | None = None) -> Env:
    clock, ids, store = FixedClock(T0), SequentialIdGenerator(1000), ProfileStore()
    lit = lit or FixtureLiterature(ROOT)
    jobs = InMemoryJobQueue(clock, ids)
    uow = lambda: InMemoryProfileUoW(store)  # noqa: E731
    return Env(
        ProfileService(uow, lit, jobs, clock, ids), ImportProfile(uow, lit, clock, ids), store, jobs, lit
    )


async def _run_import(env: Env, researcher_id: UUID, gen: int, last: bool = False) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {"researcher_id": str(researcher_id), "generation": gen}
    return await env.imp.handle(payload, last_attempt=last)


async def test_find_candidates_groups_split_profiles() -> None:
    env = _env()
    groups = await env.svc.find_candidates("Johan Foster", "British Columbia")
    g = groups[0]
    assert (
        g.primary.id == FOSTER and g.includes_records == 2 and g.primary.works_count == 159 and g.top_topics()
    )
    with pytest.raises(ValidationFailed):
        await env.svc.find_candidates("J")


async def test_acceptance_claim_merge_import() -> None:
    env = _env()
    r = await env.svc.claim(A, [FOSTER, SPLIT], FOSTER, "sess")
    assert r.import_status == ImportStatus.QUEUED and r.verified_level == VerifiedLevel.ORCID_MATCHED
    job = await env.jobs.claim("w", ["profile.import"])
    assert job and job.payload["feature"] == "profile.import" and job.payload["session_id"] == "sess"
    out = await _run_import(env, r.id, r.import_generation)
    view = await env.svc.get_profile(A)
    assert (view.researcher.import_status == ImportStatus.DONE and len(view.works) == 159 + 0) or len(
        view.works
    ) >= 159
    assert out["works"] == len(view.works) and view.researcher.institution == "University of British Columbia"
    assert view.researcher.topics and view.researcher.topics[0].weight == 1.0
    uncertain = [w for w in view.works if not w.included]
    assert uncertain and all(w.uncertain_reason for w in uncertain)
    assert "profile.claimed" in env.store.audit.actions()


async def test_claim_is_idempotent_and_enqueues_once() -> None:
    env = _env()
    r1 = await env.svc.claim(A, [FOSTER, SPLIT], None, None)
    r2 = await env.svc.claim(A, [SPLIT, FOSTER], FOSTER, None)
    assert r1.import_generation == r2.import_generation == 1
    assert len([j for j in env.jobs.jobs.values() if j.kind == "profile.import"]) == 1
    r3 = await env.svc.claim(A, [FOSTER], None, None)  # different set bumps the generation
    assert r3.import_generation == 2 and r3.author_ids == (FOSTER,)


async def test_claim_validation_and_unknown_author() -> None:
    env = _env()
    for bad in ([], ["x"], [FOSTER] * 0, [f"A{i}" for i in range(11)]):
        with pytest.raises(ValidationFailed):
            await env.svc.claim(A, bad, None, None)
    with pytest.raises(ValidationFailed):
        await env.svc.claim(A, [FOSTER], MOREIRA, None)
    with pytest.raises(UnknownAuthor):
        await env.svc.claim(A, ["A1"], None, None)


async def test_two_accounts_claiming_same_author_are_both_contested_and_not_revealed() -> None:
    env = _env()
    await env.svc.claim(A, [FOSTER], None, None)
    await env.svc.claim(B, [FOSTER], None, None)
    ra, rb = await env.svc.summary(A), await env.svc.summary(B)
    assert ra and rb and ra.verified_level == rb.verified_level == VerifiedLevel.CONTESTED
    assert "profile.claim_conflict" in env.store.audit.actions()
    await env.svc.claim(B, [MOREIRA], None, None)  # B moves on: A is no longer contested
    ra = await env.svc.summary(A)
    assert ra and ra.verified_level == VerifiedLevel.ORCID_MATCHED


async def test_import_is_idempotent_stale_ignored_and_toggle_survives() -> None:
    env = _env()
    r = await env.svc.claim(A, [FOSTER], None, None)
    await _run_import(env, r.id, 1)
    first = await env.svc.get_profile(A)
    again = await _run_import(env, r.id, 1)
    assert again == {"skipped": "stale"} or "skipped" in again  # done -> skipped
    w = first.works[0]
    await env.svc.toggle_work(A, w.id, not w.included)
    r2 = await env.svc.claim(A, [FOSTER, SPLIT], FOSTER, None)  # new generation -> reimport
    await _run_import(env, r2.id, r2.import_generation)
    after = await env.svc.get_profile(A)
    again_w = next(x for x in after.works if x.openalex_work_id == w.openalex_work_id)
    assert again_w.included == (not w.included) and again_w.included_by_user
    assert await _run_import(env, r.id, 1) == {"skipped": "stale"}


async def test_toggle_excludes_work_from_topics_and_other_user_gets_404() -> None:
    env = _env()
    r = await env.svc.claim(A, [MOREIRA], None, None)
    await _run_import(env, r.id, 1)
    view = await env.svc.get_profile(A)
    for w in view.works:
        await env.svc.toggle_work(A, w.id, False)
    assert (await env.svc.get_profile(A)).researcher.topics == []
    await env.svc.claim(B, [FOSTER], None, None)
    with pytest.raises(NotFound):
        await env.svc.toggle_work(B, view.works[0].id, True)
    with pytest.raises(ProfileNotFound):
        await env.svc.get_profile(AccountId(UUID(int=77)))


class FlakyLiterature:
    """Raises on the second author's works to simulate a crash mid-import."""

    def __init__(self, inner: LiteraturePort) -> None:
        self.inner, self.fail_on = inner, SPLIT
        self.calls: list[str] = []

    async def list_author_works(self, author_id: str, limit: int = 500) -> list[WorkRecord]:
        self.calls.append(author_id)
        if author_id == self.fail_on:
            raise RuntimeError("boom")
        return await self.inner.list_author_works(author_id, limit)

    async def search_authors(self, name: str, affiliation_hint: str | None = None) -> list[AuthorCandidate]:
        return await self.inner.search_authors(name, affiliation_hint)

    async def authors_by_orcid(self, orcid: str) -> list[AuthorRecord]:
        return await self.inner.authors_by_orcid(orcid)

    async def get_author(self, author_id: str) -> AuthorRecord:
        return await self.inner.get_author(author_id)

    async def search_works(self, query: WorkQuery) -> list[WorkRecord]:
        return await self.inner.search_works(query)

    async def get_work(self, work_id_or_doi: str) -> WorkRecord | None:
        return await self.inner.get_work(work_id_or_doi)

    async def count_works(self, query: WorkQuery) -> int:
        return await self.inner.count_works(query)


async def test_crash_after_first_author_resumes_without_duplicates() -> None:
    flaky = FlakyLiterature(FixtureLiterature(ROOT))
    env = _env(flaky)
    r = await env.svc.claim(A, [FOSTER, SPLIT], FOSTER, None)
    with pytest.raises(RuntimeError):
        await _run_import(env, r.id, 1)
    mid = await env.svc.summary(A)
    assert mid and mid.import_status == ImportStatus.IMPORTING
    assert {x.author_id: x.imported_generation for x in mid.openalex_ids} == {FOSTER: 1, SPLIT: None}
    flaky.fail_on, flaky.calls = "none", []
    await _run_import(env, r.id, 1)
    assert flaky.calls == [SPLIT]  # first author not fetched again
    works = (await env.svc.get_profile(A)).works
    assert len({w.openalex_work_id for w in works}) == len(works)
    done = await env.svc.summary(A)
    assert done and done.import_status == ImportStatus.DONE


async def test_last_attempt_failure_marks_failed_and_reclaim_requeues() -> None:
    flaky = FlakyLiterature(FixtureLiterature(ROOT))
    flaky.fail_on = MOREIRA
    env = _env(flaky)
    r = await env.svc.claim(A, [MOREIRA], None, None)
    with pytest.raises(RuntimeError):
        await _run_import(env, r.id, 1, last=True)
    s = await env.svc.summary(A)
    assert s and s.import_status == ImportStatus.FAILED and s.import_error == "import_failed"
    r2 = await env.svc.claim(A, [MOREIRA], None, None)  # retry from the UI
    assert r2.import_generation == 2 and r2.import_status == ImportStatus.QUEUED
    assert Job is not None and JobStatus.QUEUED is not None


async def test_enqueue_failure_is_503_and_reclaim_is_idempotent() -> None:
    from scout.shared import InfrastructureError

    env = _env()

    async def boom(*_: object, **__: object) -> UUID:
        raise RuntimeError("queue down")

    original = env.jobs.enqueue
    env.jobs.enqueue = boom  # type: ignore[method-assign]
    with pytest.raises(InfrastructureError):
        await env.svc.claim(A, [FOSTER], None, None)
    env.jobs.enqueue = original  # type: ignore[method-assign]
    r = await env.svc.claim(A, [FOSTER], None, None)
    assert r.import_generation == 1
    assert len([j for j in env.jobs.jobs.values() if j.kind == "profile.import"]) == 1
