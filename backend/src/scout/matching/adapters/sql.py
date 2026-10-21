"""SQL adapters of the matching context. Identity is read only through ``match_reveal_v`` (RevealRepo)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from scout.adapters.persistence import models as m
from scout.adapters.persistence.uow import SqlUnitOfWork
from scout.matching.domain.enums import (
    CloseReason,
    Decision,
    IdeasStatus,
    MatchState,
    Side,
)
from scout.matching.domain.proposal import MatchProposal, PublicFacet, ScoreBasis, StoredRationale
from scout.matching.domain.reveal import Contact
from scout.shared import AccountId

_PENDING = (MatchState.PROPOSED.value, MatchState.A_ACCEPTED.value, MatchState.B_ACCEPTED.value)


def _decision(v: str | None) -> Decision | None:
    return Decision(v) if v else None


def _side(v: str | None) -> Side | None:
    return Side(v) if v else None


def _proposal(r: Any) -> MatchProposal:
    return MatchProposal(
        id=r.id, a_id=AccountId(r.a_id), b_id=AccountId(r.b_id), field=r.field, score=r.score,
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
            "a_id",
            "b_id",
            "field",
            "score",
            "score_basis",
            "rationale",
            "a_public",
            "b_public",
            "created_at",
        ):
            v.pop(k)
        v["updated_at"] = func.now()
        await self._s.execute(update(t).where(t.c.id == proposal.id).values(**v))

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

    def _bind(self) -> None:
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
