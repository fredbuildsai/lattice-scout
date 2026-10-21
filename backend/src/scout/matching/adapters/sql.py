"""SQL adapters of the matching context. Identity is read only through ``match_reveal_v`` (RevealRepo)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.matching.domain.agent_io import JointIdea
from scout.matching.domain.enums import (
    CloseReason,
    Decision,
    IdeasStatus,
    MatchState,
    Side,
)
from scout.matching.domain.ids import idea_id
from scout.matching.domain.profile import MatchProfile, TopicWeight
from scout.matching.domain.proposal import BringItem, MatchProposal, PublicFacet, ScoreBasis, StoredRationale
from scout.matching.domain.reveal import Contact
from scout.shared import AccountId

_PENDING = (MatchState.PROPOSED.value, MatchState.A_ACCEPTED.value, MatchState.B_ACCEPTED.value)


def _decision(v: str | None) -> Decision | None:
    return Decision(v) if v else None


def _side(v: str | None) -> Side | None:
    return Side(v) if v else None


def _proposal(r: Any) -> MatchProposal:
    return MatchProposal(
        id=r.id, a_id=AccountId(r.a_id) if r.a_id else None, b_id=AccountId(r.b_id) if r.b_id else None, field=r.field, score=r.score,
        score_basis=ScoreBasis.model_validate(r.score_basis), rationale=StoredRationale.model_validate(r.rationale),
        a_public=PublicFacet.model_validate(r.a_public), b_public=PublicFacet.model_validate(r.b_public),
        created_at=r.created_at, expires_at=r.expires_at, state=MatchState(r.state),
        a_decision=_decision(r.a_decision), b_decision=_decision(r.b_decision), a_decided_at=r.a_decided_at,
        b_decided_at=r.b_decided_at, declined_by=_side(r.declined_by),
        close_reason=CloseReason(r.close_reason) if r.close_reason else None,
        never_similar_by=_side(r.never_similar_by), revealed_at=r.revealed_at, closed_at=r.closed_at,
        ideas_status=IdeasStatus(r.ideas_status),
    )  # fmt: skip


def _values(p: MatchProposal) -> dict[str, Any]:
    return dict(
        id=p.id, a_id=p.a_id, b_id=p.b_id, field=p.field, score=p.score,
        score_basis=p.score_basis.model_dump(mode="json"), rationale=p.rationale.model_dump(mode="json"),
        a_public=p.a_public.model_dump(mode="json"), b_public=p.b_public.model_dump(mode="json"),
        state=p.state.value, a_decision=p.a_decision.value if p.a_decision else None,
        b_decision=p.b_decision.value if p.b_decision else None, a_decided_at=p.a_decided_at,
        b_decided_at=p.b_decided_at, declined_by=p.declined_by.value if p.declined_by else None,
        close_reason=p.close_reason.value if p.close_reason else None,
        never_similar_by=p.never_similar_by.value if p.never_similar_by else None, created_at=p.created_at,
        expires_at=p.expires_at, revealed_at=p.revealed_at, closed_at=p.closed_at,
        ideas_status=p.ideas_status.value, updated_at=p.closed_at or p.revealed_at or p.created_at,
    )  # fmt: skip


class SqlMatchRepo:
    """Implements ``MatchRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, proposal: MatchProposal) -> bool:
        """Insert; False on a duplicate id or active pair."""
        stmt = pg_insert(m.match_proposal).values(**_values(proposal)).on_conflict_do_nothing()
        res = await self._s.execute(stmt.returning(m.match_proposal.c.id))
        return res.first() is not None

    async def _get(self, match_id: UUID, viewer: AccountId, lock: bool) -> MatchProposal | None:
        t = m.match_proposal
        q = select(t).where(t.c.id == match_id, or_(t.c.a_id == viewer, t.c.b_id == viewer))
        row = (await self._s.execute(q.with_for_update() if lock else q)).first()
        return _proposal(row) if row else None

    async def get(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        """Participant's proposal."""
        return await self._get(match_id, viewer, False)

    async def get_for_update(self, match_id: UUID, viewer: AccountId) -> MatchProposal | None:
        """Participant's proposal, locked."""
        return await self._get(match_id, viewer, True)

    async def save(self, proposal: MatchProposal) -> None:
        """Persist the mutable columns."""
        t = m.match_proposal
        v = _values(proposal)
        for k in (
            "id",
            "field",
            "score",
            "score_basis",
            "a_public",
            "b_public",
            "created_at",
        ):
            v.pop(k)
        v["updated_at"] = func.now()
        await self._s.execute(update(t).where(t.c.id == proposal.id).values(**v))

    async def involving_for_update(self, account: AccountId) -> list[MatchProposal]:
        """Every proposal of ``account``, row-locked."""
        t = m.match_proposal
        q = select(t).where(or_(t.c.a_id == account, t.c.b_id == account)).order_by(t.c.id).with_for_update()
        return [_proposal(r) for r in await self._s.execute(q)]

    async def delete(self, match_ids: Sequence[UUID]) -> None:
        """Delete proposals; ``joint_idea`` cascades."""
        if match_ids:
            await self._s.execute(delete(m.match_proposal).where(m.match_proposal.c.id.in_(list(match_ids))))

    async def purge_detached(self, closed_before: datetime) -> int:
        """Delete detached proposals that expired before ``closed_before``."""
        t = m.match_proposal
        res = await self._s.execute(
            delete(t).where(or_(t.c.a_id.is_(None), t.c.b_id.is_(None)), t.c.expires_at < closed_before)
        )
        return int(res.rowcount or 0)  # type: ignore[attr-defined]

    async def list_for(self, viewer: AccountId, limit: int, offset: int) -> list[MatchProposal]:
        """Newest first."""
        t = m.match_proposal
        q = (
            select(t).where(or_(t.c.a_id == viewer, t.c.b_id == viewer))
            .order_by(t.c.created_at.desc(), t.c.id).limit(limit).offset(offset)
        )  # fmt: skip
        return [_proposal(r) for r in await self._s.execute(q)]

    async def due_for_expiry(self, now: datetime, limit: int) -> list[UUID]:
        """Pending proposals past their deadline."""
        t = m.match_proposal
        q = (
            select(t.c.id)
            .where(t.c.state.in_(_PENDING), t.c.expires_at <= now)
            .order_by(t.c.expires_at)
            .limit(limit)
        )
        return [r.id for r in await self._s.execute(q)]

    async def count_created_since(self, account: AccountId, since: datetime) -> int:
        """Proposals created for ``account`` since ``since``."""
        t = m.match_proposal
        q = (
            select(func.count())
            .select_from(t)
            .where(or_(t.c.a_id == account, t.c.b_id == account), t.c.created_at >= since)
        )
        return int((await self._s.execute(q)).scalar_one())

    async def open_counts(self, accounts: Sequence[AccountId], since: datetime) -> dict[AccountId, int]:
        """Proposals created for each account since ``since``."""
        out: dict[AccountId, int] = {a: 0 for a in accounts}
        if not accounts:
            return out
        rows = await self._s.execute(
            text(
                "SELECT x.account_id, count(*) AS n FROM match_proposal p "
                "CROSS JOIN LATERAL (VALUES (p.a_id), (p.b_id)) AS x(account_id) "
                "WHERE p.created_at >= :since AND x.account_id = ANY(:ids) GROUP BY 1"
            ),
            {"since": since, "ids": list(accounts)},
        )
        for r in rows:
            out[AccountId(r.account_id)] = int(r.n)
        return out

    async def pending_outside_pool(self, limit: int) -> list[UUID]:
        """Pending proposals where a party is no longer in the pool."""
        rows = await self._s.execute(
            text(
                "SELECT id FROM match_proposal WHERE state IN ('proposed','a_accepted','b_accepted') AND "
                "(a_id NOT IN (SELECT account_id FROM match_pool_v) OR b_id NOT IN (SELECT account_id FROM match_pool_v)) "
                "ORDER BY created_at LIMIT :n"
            ),
            {"n": limit},
        )
        return [r.id for r in rows]

    async def ideas_pending_before(self, before: datetime, limit: int) -> list[UUID]:
        """Mutual matches with ideas pending since before ``before``."""
        t = m.match_proposal
        q = (
            select(t.c.id)
            .where(t.c.state == "mutual", t.c.ideas_status == "pending", t.c.revealed_at < before)
            .limit(limit)
        )
        return [r.id for r in await self._s.execute(q)]

    async def get_any(self, match_id: UUID, lock: bool = False) -> MatchProposal | None:
        """Any participant's view, optionally locked (worker side)."""
        t = m.match_proposal
        q = select(t).where(t.c.id == match_id)
        row = (await self._s.execute(q.with_for_update() if lock else q)).first()
        return _proposal(row) if row else None


class SqlMatchProfileRepo:
    """Implements ``MatchProfileRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _load(self, rows: Sequence[Any]) -> dict[AccountId, MatchProfile]:
        ids = [r.account_id for r in rows]
        topics: dict[UUID, list[TopicWeight]] = {}
        methods: dict[UUID, dict[str, float]] = {}
        sources: dict[UUID, dict[str, str]] = {}
        if ids:
            for t in await self._s.execute(
                select(m.match_profile_topic).where(m.match_profile_topic.c.account_id.in_(ids)).order_by(
                    m.match_profile_topic.c.weight.desc(), m.match_profile_topic.c.topic_id)
            ):  # fmt: skip
                topics.setdefault(t.account_id, []).append(TopicWeight(t.topic_id, t.name, t.weight))
            for x in await self._s.execute(
                select(m.match_profile_method).where(m.match_profile_method.c.account_id.in_(ids))
            ):
                methods.setdefault(x.account_id, {})[x.method_id] = x.weight
                sources.setdefault(x.account_id, {})[x.method_id] = x.source
        return {
            AccountId(r.account_id): MatchProfile(
                account_id=r.account_id, researcher_id=r.researcher_id, field=r.field, region=r.region,
                career_band=r.career_band, activity=r.activity, eligible=r.eligible,
                topics=tuple(topics.get(r.account_id, [])), methods=methods.get(r.account_id, {}),
                method_sources=sources.get(r.account_id, {}), topics_hash=r.topics_hash, methods_hash=r.methods_hash,
                vocabulary_version=r.vocabulary_version, methods_prompt_version=r.methods_prompt_version,
                uses_vault_signals=r.uses_vault_signals, computed_at=r.computed_at,
            )
            for r in rows
        }  # fmt: skip

    async def get(self, account: AccountId) -> MatchProfile | None:
        """Stored profile."""
        return (await self.get_many([account])).get(account)

    async def get_many(self, accounts: Sequence[AccountId]) -> dict[AccountId, MatchProfile]:
        """Stored profiles."""
        t = m.match_profile
        rows = list(await self._s.execute(select(t).where(t.c.account_id.in_(list(accounts)))))
        return await self._load(rows)

    async def save(self, profile: MatchProfile, proposals: Sequence[tuple[str, tuple[str, ...]]]) -> None:
        """Replace the profile and its rows."""
        await self.delete(AccountId(profile.account_id))
        await self._s.execute(
            insert(m.match_profile).values(
                account_id=profile.account_id, researcher_id=profile.researcher_id, field=profile.field,
                region=profile.region, career_band=profile.career_band, activity=profile.activity,
                eligible=profile.eligible, topics_hash=profile.topics_hash, methods_hash=profile.methods_hash,
                vocabulary_version=profile.vocabulary_version, methods_prompt_version=profile.methods_prompt_version,
                uses_vault_signals=profile.uses_vault_signals, computed_at=profile.computed_at,
                created_at=profile.computed_at, updated_at=profile.computed_at,
            )
        )  # fmt: skip
        if profile.topics:
            await self._s.execute(
                insert(m.match_profile_topic),
                [
                    dict(account_id=profile.account_id, topic_id=t.id, name=t.name, weight=t.weight)
                    for t in profile.topics
                ],
            )
        if profile.methods:
            await self._s.execute(
                insert(m.match_profile_method),
                [
                    dict(
                        account_id=profile.account_id,
                        method_id=k,
                        weight=v,
                        source=profile.method_sources.get(k, "abstract"),
                    )
                    for k, v in profile.methods.items()
                ],
            )
        from uuid import uuid5

        from scout.matching.domain.ids import MATCHING_NS

        for label, refs in proposals:
            stmt = pg_insert(m.match_method_proposal).values(
                id=uuid5(MATCHING_NS, f"{profile.account_id}:method:{label.lower()}"), account_id=profile.account_id,
                label=label, source_refs=list(refs), created_at=profile.computed_at,
            )  # fmt: skip
            await self._s.execute(stmt.on_conflict_do_nothing())

    async def delete(self, account: AccountId) -> None:
        """Remove a profile and its rows."""
        for t in (m.match_profile_method, m.match_profile_topic):
            await self._s.execute(t.delete().where(t.c.account_id == account))
        await self._s.execute(m.match_profile.delete().where(m.match_profile.c.account_id == account))

    async def accounts(self) -> list[AccountId]:
        """Accounts with a profile."""
        return [AccountId(r.account_id) for r in await self._s.execute(select(m.match_profile.c.account_id))]


class SqlCandidateQuery:
    """Implements ``CandidateQuery``: topic overlap in SQL, then the exclusions."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def candidates(
        self, *, field: str, account: AccountId | None, exclude_coauthors: bool, exclude_same_group: bool,
        cooldown_since: datetime, limit: int, per_person: int = 50,
    ) -> list[tuple[AccountId, AccountId]]:  # fmt: skip
        """Canonical pairs, best overlap first; each person keeps at most ``per_person`` best pairs (DECISIONS 80)."""
        reasons = [r for r, on in (("coauthor", exclude_coauthors), ("same_group", exclude_same_group)) if on]
        rows = await self._s.execute(
            text(
                "WITH c AS ("
                " SELECT x.account_id AS a, y.account_id AS b, SUM(LEAST(x.weight, y.weight)) AS inter"
                " FROM match_profile_topic x"
                " JOIN match_profile_topic y ON y.topic_id = x.topic_id AND y.account_id > x.account_id"
                " JOIN match_pool_v px ON px.account_id = x.account_id AND px.field = :field"
                " JOIN match_pool_v py ON py.account_id = y.account_id AND py.field = :field"
                " WHERE (CAST(:acct AS uuid) IS NULL OR x.account_id = CAST(:acct AS uuid) OR y.account_id = CAST(:acct AS uuid))"
                " GROUP BY 1, 2),"
                " ok AS (SELECT c.a, c.b, c.inter FROM c"
                "  WHERE NOT EXISTS (SELECT 1 FROM match_pair_conflict_v k WHERE k.x = c.a AND k.y = c.b AND k.reason = ANY(:reasons))"
                "  AND NOT EXISTS (SELECT 1 FROM match_block bl WHERE (bl.account_id = c.a AND bl.blocked_account_id = c.b)"
                "    OR (bl.account_id = c.b AND bl.blocked_account_id = c.a))"
                "  AND NOT EXISTS (SELECT 1 FROM match_proposal p WHERE p.a_id = c.a AND p.b_id = c.b"
                "    AND (p.state IN ('proposed','a_accepted','b_accepted','mutual') OR p.created_at > :cooldown))),"
                " dir AS (SELECT a AS me, a, b, inter FROM ok UNION ALL SELECT b, a, b, inter FROM ok),"
                " rk AS (SELECT a, b, inter, ROW_NUMBER() OVER (PARTITION BY me ORDER BY inter DESC, a, b) AS r FROM dir)"
                " SELECT a, b FROM rk GROUP BY a, b, inter HAVING MIN(r) <= :per"
                " ORDER BY inter DESC, a, b LIMIT :limit"
            ),
            {
                "field": field,
                "acct": account,
                "reasons": reasons,
                "cooldown": cooldown_since,
                "limit": limit,
                "per": per_person,
            },
        )
        return [(AccountId(r.a), AccountId(r.b)) for r in rows]


class SqlJointIdeaRepo:
    """Implements ``JointIdeaRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add_many(
        self, match_id: UUID, ideas: Sequence[JointIdea], prompt_version: str, model: str, now: datetime
    ) -> None:
        """Idempotent insert; the database refuses ideas for a non-mutual match."""
        for i, idea in enumerate(ideas):
            stmt = pg_insert(m.joint_idea).values(
                id=idea_id(match_id, i), match_id=match_id, match_state="mutual", ord=i, title=idea.title,
                question=idea.question, uses_from_a=[b.model_dump(mode="json") for b in idea.uses_from_a],
                uses_from_b=[b.model_dump(mode="json") for b in idea.uses_from_b], first_step=idea.first_step,
                possible_output=idea.possible_output, prompt_version=prompt_version, model=model, created_at=now,
            )  # fmt: skip
            await self._s.execute(stmt.on_conflict_do_nothing())

    async def list(self, match_id: UUID) -> list[JointIdea]:
        """Ideas in order."""
        t = m.joint_idea
        return [
            JointIdea(
                title=r.title, question=r.question, uses_from_a=[BringItem.model_validate(x) for x in r.uses_from_a],
                uses_from_b=[BringItem.model_validate(x) for x in r.uses_from_b], first_step=r.first_step,
                possible_output=r.possible_output,
            )
            for r in await self._s.execute(select(t).where(t.c.match_id == match_id).order_by(t.c.ord))
        ]  # fmt: skip


class SqlRevealRepo:
    """Implements ``RevealRepo`` over ``match_reveal_v`` only."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def contact(self, match_id: UUID, viewer: AccountId) -> Contact | None:
        """Contact of the other side; the view has rows only for mutual matches."""
        row = (
            await self._s.execute(
                text(
                    "SELECT v.display_name, v.institution, v.orcid, v.email, v.openalex_ids, "
                    "CASE WHEN p.a_id = v.other_id THEN p.a_public ELSE p.b_public END AS facet "
                    "FROM match_reveal_v v JOIN match_proposal p ON p.id = v.match_id "
                    "WHERE v.match_id = :m AND v.viewer_id = :v"
                ),
                {"m": match_id, "v": viewer},
            )
        ).first()
        if row is None:
            return None
        facet = PublicFacet.model_validate(row.facet)
        return Contact(
            display_name=row.display_name, institution=row.institution, email=row.email, orcid=row.orcid,
            openalex_ids=list(row.openalex_ids), region=facet.region, career_band=facet.career_band,
        )  # fmt: skip


class SqlBlockRepo:
    """Implements ``BlockRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, account: AccountId, blocked: AccountId, match_id: UUID, now: datetime) -> None:
        """Idempotent."""
        stmt = pg_insert(m.match_block).values(
            account_id=account, blocked_account_id=blocked, match_id=match_id, created_at=now
        )
        await self._s.execute(stmt.on_conflict_do_nothing())


class SqlMatchUnitOfWork(SqlUnitOfWork):
    """Unit of work over one session."""

    _matches: SqlMatchRepo
    _reveal: SqlRevealRepo
    _blocks: SqlBlockRepo
    _profiles: SqlMatchProfileRepo
    _candidates: SqlCandidateQuery
    _ideas: SqlJointIdeaRepo

    def _bind(self) -> None:
        self._profiles = SqlMatchProfileRepo(self.session)
        self._candidates = SqlCandidateQuery(self.session)
        self._ideas = SqlJointIdeaRepo(self.session)
        self._matches = SqlMatchRepo(self.session)
        self._reveal = SqlRevealRepo(self.session)
        self._blocks = SqlBlockRepo(self.session)

    @property
    def matches(self) -> SqlMatchRepo:
        """Proposals."""
        return self._matches

    @property
    def reveal(self) -> SqlRevealRepo:
        """Contacts."""
        return self._reveal

    @property
    def blocks(self) -> SqlBlockRepo:
        """Blocks."""
        return self._blocks

    @property
    def profiles(self) -> SqlMatchProfileRepo:
        """Profiles."""
        return self._profiles

    @property
    def candidates(self) -> SqlCandidateQuery:
        """Candidate query."""
        return self._candidates

    @property
    def ideas(self) -> SqlJointIdeaRepo:
        """Joint ideas."""
        return self._ideas
