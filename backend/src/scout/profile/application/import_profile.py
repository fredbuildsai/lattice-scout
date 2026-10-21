"""ImportProfile job handler (``profile.import``): works, topics, institution; at-least-once and resumable."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import structlog
from pydantic import JsonValue

from scout.ports.literature import LiteraturePort
from scout.profile.application.mapping import to_claimed_work
from scout.profile.application.service import ProfileConfig
from scout.profile.domain.researcher import ImportStatus
from scout.profile.domain.topics import compute_topics
from scout.profile.ports.repos import ProfileUnitOfWork
from scout.shared import Clock, IdGenerator, NotFound

log = structlog.get_logger()


class ImportProfile:
    """Import the works of every claimed record into ``claimed_work`` and compute topics."""

    def __init__(
        self,
        uow: Callable[[], ProfileUnitOfWork],
        literature: LiteraturePort,
        clock: Clock,
        ids: IdGenerator,
        cfg: ProfileConfig = ProfileConfig(),  # noqa: B008
    ) -> None:
        self._uow, self._lit, self._clock, self._ids, self._cfg = uow, literature, clock, ids, cfg

    async def handle(self, payload: dict[str, JsonValue], *, last_attempt: bool) -> dict[str, JsonValue]:
        """Run the import for ``payload['researcher_id']`` / ``generation``."""
        rid, gen = UUID(str(payload["researcher_id"])), int(str(payload["generation"]))
        async with self._uow() as uow:
            r = await uow.researchers.get_for_update(rid)
            if r is None or r.import_generation != gen or r.import_status == ImportStatus.DONE:
                return {"skipped": "stale" if r else "missing"}
            r.start_import(gen, self._clock.now())
            await uow.researchers.save(r)
            await uow.commit()
            todo = [(x.author_id, x.imported_generation) for x in r.openalex_ids]
            variants = [x.display_name for x in r.openalex_ids]
        try:
            for author_id, done_gen in todo:
                if done_gen == gen:
                    continue
                try:
                    records = await self._lit.list_author_works(
                        author_id, self._cfg.works_limit
                    )  # no transaction open
                except NotFound:
                    log.warning("import_author_missing", author_id=author_id)
                    records = []
                async with self._uow() as uow:
                    r = await uow.researchers.get_for_update(rid)
                    if r is None or r.import_generation != gen:
                        return {"skipped": "stale"}
                    existing = {w.openalex_work_id: w for w in await uow.works.list_for(rid)}
                    upserts = []
                    for rec in records:
                        fresh, reason = to_claimed_work(rec, rid, self._ids.new(), author_id, variants)
                        if (cur := existing.get(rec.id)) is not None:
                            cur.refresh(fresh, author_id, reason)
                            upserts.append(cur)
                        else:
                            upserts.append(fresh)
                    await uow.works.upsert_many(upserts)
                    next(x for x in r.openalex_ids if x.author_id == author_id).imported_generation = gen
                    await uow.researchers.save(r)
                    await uow.commit()
            async with self._uow() as uow:  # read-only peek to learn the primary id
                peek = await uow.researchers.get(rid)
            if peek is None or peek.import_generation != gen:
                return {"skipped": "stale"}
            try:
                snapshot = await self._lit.get_author(peek.primary_author_id)  # no transaction open
                institution = snapshot.institutions[0] if snapshot.institutions else None
                country = snapshot.country
            except NotFound:
                institution = country = None
            async with self._uow() as uow:
                r = await uow.researchers.get_for_update(rid)
                if r is None or r.import_generation != gen:
                    return {"skipped": "stale"}
                await uow.works.delete_unsourced(rid, r.author_ids)
                works = await uow.works.list_for(rid)
                r.finish_import(
                    gen, compute_topics(works, self._cfg.max_topics), institution, country, self._clock.now()
                )
                await uow.researchers.save(r)
                await uow.commit()
        except Exception:
            if last_attempt:
                async with self._uow() as uow:
                    r = await uow.researchers.get_for_update(rid)
                    if r is not None and r.fail_import(gen, "import_failed", self._clock.now()):
                        await uow.researchers.save(r)
                        await uow.commit()
            raise
        return {"authors": len(todo), "works": len(works), "included": sum(w.included for w in works)}
