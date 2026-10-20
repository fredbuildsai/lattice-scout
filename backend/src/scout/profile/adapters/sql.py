"""SQL repositories for the profile context."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID, uuid4

from sqlalchemy import case, delete, func, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.audit import SqlAuditLog
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.ports.audit import AuditLog
from scout.profile.domain.grouping import RecordBasis, TopicWeight
from scout.profile.domain.researcher import ImportStatus, Researcher, ResearcherOpenAlexId, VerifiedLevel
from scout.profile.domain.topics import ResearcherTopic
from scout.profile.domain.work import ClaimedWork, UncertainReason
from scout.shared import AccountId


class SqlResearcherRepo:
    """Implements ``ResearcherRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _load(self, rows: list[object]) -> list[Researcher]:
        out: list[Researcher] = []
        for row in rows:
            r = row._mapping  # type: ignore[attr-defined]
            ids = (
                await self._s.execute(
                    select(m.researcher_openalex_id)
                    .where(m.researcher_openalex_id.c.researcher_id == r["id"])
                    .order_by(m.researcher_openalex_id.c.position)
                )
            ).all()
            topics = (
                await self._s.execute(
                    select(m.researcher_topic)
                    .where(m.researcher_topic.c.researcher_id == r["id"])
                    .order_by(m.researcher_topic.c.weight.desc(), m.researcher_topic.c.openalex_topic_id)
                )
            ).all()
            out.append(
                Researcher(
                    id=r["id"],
                    account_id=AccountId(r["account_id"]),
                    display_name=r["display_name"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    orcid=r["orcid"],
                    institution=r["institution"],
                    country=r["country"],
                    career_stage=r["career_stage"],
                    verified_level=VerifiedLevel(r["verified_level"]),
                    import_status=ImportStatus(r["import_status"]),
                    import_generation=r["import_generation"],
                    import_error=r["import_error"],
                    import_requested_at=r["import_requested_at"],
                    imported_at=r["imported_at"],
                    openalex_ids=[
                        ResearcherOpenAlexId(
                            x._mapping["openalex_author_id"],
                            x._mapping["merged"],
                            RecordBasis(x._mapping["basis"]),
                            x._mapping["display_name"],
                            x._mapping["orcid"],
                            x._mapping["works_count"],
                            x._mapping["imported_generation"],
                        )
                        for x in ids
                    ],
                    topics=[
                        ResearcherTopic(
                            topic_id=x._mapping["openalex_topic_id"],
                            name=x._mapping["name"],
                            subfield=x._mapping["subfield"],
                            weight=min(1.0, max(0.0, x._mapping["weight"])),
                        )
                        for x in topics
                    ],
                )
            )
        return out

    async def get_by_account(self, account_id: AccountId) -> Researcher | None:
        """The researcher of an account."""
        rows = (
            await self._s.execute(select(m.researcher).where(m.researcher.c.account_id == account_id))
        ).all()
        found = await self._load(list(rows))
        return found[0] if found else None

    async def get(self, researcher_id: UUID) -> Researcher | None:
        """A researcher by id."""
        rows = (await self._s.execute(select(m.researcher).where(m.researcher.c.id == researcher_id))).all()
        found = await self._load(list(rows))
        return found[0] if found else None

    async def get_for_update(self, researcher_id: UUID) -> Researcher | None:
        """Row-locking read."""
        rows = (
            await self._s.execute(
                select(m.researcher).where(m.researcher.c.id == researcher_id).with_for_update()
            )
        ).all()
        found = await self._load(list(rows))
        return found[0] if found else None

    async def lock_authors(self, author_ids: Collection[str]) -> None:
        """``pg_advisory_xact_lock`` per author id, in sorted order (no deadlocks between claimants)."""
        for aid in sorted(set(author_ids)):
            await self._s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"author:{aid}"})

    async def find_by_author_ids(self, author_ids: Collection[str]) -> list[Researcher]:
        """Every researcher that claimed one of the ids."""
        if not author_ids:
            return []
        sub = select(m.researcher_openalex_id.c.researcher_id).where(
            m.researcher_openalex_id.c.openalex_author_id.in_(list(author_ids))
        )
        rows = (await self._s.execute(select(m.researcher).where(m.researcher.c.id.in_(sub)))).all()
        return await self._load(list(rows))

    @staticmethod
    def _row(r: Researcher) -> dict[str, object]:
        return {
            "account_id": r.account_id, "display_name": r.display_name, "orcid": r.orcid,
            "institution": r.institution, "country": r.country, "career_stage": r.career_stage,
            "verified_level": r.verified_level.value, "import_status": r.import_status.value,
            "import_generation": r.import_generation, "import_error": r.import_error,
            "import_requested_at": r.import_requested_at, "imported_at": r.imported_at, "updated_at": r.updated_at,
        }  # fmt: skip

    async def _children(self, r: Researcher) -> None:
        await self._s.execute(
            delete(m.researcher_openalex_id).where(m.researcher_openalex_id.c.researcher_id == r.id)
        )
        await self._s.execute(delete(m.researcher_topic).where(m.researcher_topic.c.researcher_id == r.id))
        if r.openalex_ids:
            await self._s.execute(
                insert(m.researcher_openalex_id),
                [
                    {
                        "id": uuid4(),
                        "researcher_id": r.id,
                        "openalex_author_id": x.author_id,
                        "merged": x.merged,
                        "basis": x.basis.value,
                        "display_name": x.display_name,
                        "orcid": x.orcid,
                        "works_count": x.works_count,
                        "imported_generation": x.imported_generation,
                        "position": i,
                    }
                    for i, x in enumerate(r.openalex_ids)
                ],
            )
        if r.topics:
            await self._s.execute(
                insert(m.researcher_topic),
                [
                    {
                        "id": uuid4(),
                        "researcher_id": r.id,
                        "openalex_topic_id": t.topic_id,
                        "name": t.name,
                        "subfield": t.subfield,
                        "weight": t.weight,
                    }
                    for t in r.topics
                ],
            )

    async def add(self, researcher: Researcher) -> None:
        """Insert the aggregate."""
        await self._s.execute(
            insert(m.researcher).values(
                id=researcher.id, created_at=researcher.created_at, **self._row(researcher)
            )
        )
        await self._children(researcher)

    async def save(self, researcher: Researcher) -> None:
        """Update the aggregate, replacing child rows."""
        await self._s.execute(
            update(m.researcher).where(m.researcher.c.id == researcher.id).values(**self._row(researcher))
        )
        await self._children(researcher)


def _work(row: object) -> ClaimedWork:
    r = row._mapping  # type: ignore[attr-defined]
    return ClaimedWork(
        id=r["id"], researcher_id=r["researcher_id"], openalex_work_id=r["openalex_work_id"], title=r["title"],
        doi=r["doi"], year=r["year"], cited_by=r["cited_by"], type=r["type"], abstract=r["abstract"],
        topics=tuple(TopicWeight(**t) for t in r["topics"]), source_author_ids=frozenset(r["source_author_ids"]),
        included=r["included"], included_by_user=r["included_by_user"],
        uncertain_reason=UncertainReason(r["uncertain_reason"]) if r["uncertain_reason"] else None,
    )  # fmt: skip


def _work_values(w: ClaimedWork) -> dict[str, object]:
    return {
        "id": w.id, "researcher_id": w.researcher_id, "openalex_work_id": w.openalex_work_id, "doi": w.doi,
        "title": w.title, "year": w.year, "cited_by": w.cited_by, "type": w.type, "abstract": w.abstract,
        "topics": [t.model_dump() for t in w.topics], "source_author_ids": sorted(w.source_author_ids),
        "included": w.included, "included_by_user": w.included_by_user,
        "uncertain_reason": w.uncertain_reason.value if w.uncertain_reason else None,
    }  # fmt: skip


class SqlWorkRepo:
    """Implements ``ClaimedWorkRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_for(self, researcher_id: UUID) -> list[ClaimedWork]:
        """All works of a researcher."""
        rows = (
            await self._s.execute(
                select(m.claimed_work).where(m.claimed_work.c.researcher_id == researcher_id)
            )
        ).all()
        return [_work(r) for r in rows]

    async def get(self, work_id: UUID, researcher_id: UUID) -> ClaimedWork | None:
        """One work if it belongs to the researcher."""
        row = (
            await self._s.execute(
                select(m.claimed_work).where(
                    m.claimed_work.c.id == work_id, m.claimed_work.c.researcher_id == researcher_id
                )
            )
        ).first()
        return _work(row) if row else None

    async def upsert_many(self, works: list[ClaimedWork]) -> None:
        """Insert or update on ``(researcher_id, openalex_work_id)``; the existing id is kept."""
        if not works:
            return
        stmt = pg_insert(m.claimed_work).values([_work_values(w) for w in works])
        update_cols: dict[str, object] = {
            c: stmt.excluded[c]
            for c in (
                "doi",
                "title",
                "year",
                "cited_by",
                "type",
                "abstract",
                "topics",
                "source_author_ids",
                "uncertain_reason",
            )
        }
        # A concurrent user toggle always wins over the importer's (possibly stale) copy.
        update_cols["included"] = case(
            (m.claimed_work.c.included_by_user.is_(True), m.claimed_work.c.included),
            else_=stmt.excluded["included"],
        )
        update_cols["updated_at"] = func.now()
        await self._s.execute(
            stmt.on_conflict_do_update(
                constraint="claimed_work_researcher_id_openalex_work_id_key", set_=update_cols
            )
        )

    async def save(self, work: ClaimedWork) -> None:
        """Persist one work."""
        vals = _work_values(work)
        vals.pop("id")
        vals.pop("researcher_id")
        vals["updated_at"] = func.now()
        await self._s.execute(update(m.claimed_work).where(m.claimed_work.c.id == work.id).values(**vals))

    async def delete_unsourced(self, researcher_id: UUID, keep_author_ids: Collection[str]) -> int:
        """Drop source ids no longer claimed; delete works left without a source."""
        await self._s.execute(
            text(
                "UPDATE claimed_work SET source_author_ids = "
                "ARRAY(SELECT x FROM unnest(source_author_ids) x WHERE x = ANY(:keep)) WHERE researcher_id = :r"
            ),
            {"keep": list(keep_author_ids), "r": researcher_id},
        )
        res = await self._s.execute(
            text("DELETE FROM claimed_work WHERE researcher_id = :r AND cardinality(source_author_ids) = 0"),
            {"r": researcher_id},
        )
        return int(res.rowcount)  # type: ignore[attr-defined]


class SqlProfileUnitOfWork(SqlUnitOfWork):
    """Profile transaction."""

    researchers: SqlResearcherRepo
    works: SqlWorkRepo
    audit: AuditLog

    def _bind(self) -> None:
        self.researchers = SqlResearcherRepo(self.session)
        self.works = SqlWorkRepo(self.session)
        self.audit = SqlAuditLog(self.session)
