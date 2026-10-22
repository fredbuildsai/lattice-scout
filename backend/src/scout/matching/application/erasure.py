"""``MatchErasure``: remove an erased account from matching without the counterpart learning why."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from scout.matching.domain.enums import MatchState
from scout.matching.ports.repos import MatchUnitOfWork
from scout.shared import AccountId


@dataclass(frozen=True)
class ForgetResult:
    """How many proposals were detached (pending or closed) and deleted (mutual)."""

    detached: int
    deleted: int


class MatchErasure:
    """Implements the identity ``MatchErasurePort``. Must run before the account row is deleted."""

    def __init__(self, uow: Callable[[], MatchUnitOfWork]) -> None:
        self._uow = uow

    async def forget(self, account_id: UUID, now: datetime) -> ForgetResult:
        """Delete mutual proposals, detach the others (they look like a consent close to the counterpart).

        One transaction and idempotent: a second call finds nothing left that names ``account_id``.
        """
        async with self._uow() as uow:
            account = AccountId(account_id)
            rows = await uow.matches.involving_for_update(account)
            mutual = [p.id for p in rows if p.state is MatchState.MUTUAL]
            await uow.matches.delete(mutual)
            detached = 0
            for p in rows:
                if p.state is MatchState.MUTUAL:
                    continue
                p.detach(p.side_of(account), now)
                await uow.matches.save(p)
                detached += 1
            await uow.commit()
        return ForgetResult(detached=detached, deleted=len(mutual))
