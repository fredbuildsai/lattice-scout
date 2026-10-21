"""Matching identifiers. Proposal ids are uuid5 values, so a job re-run in the same ISO week creates nothing new."""

from __future__ import annotations

from datetime import datetime
from typing import NewType
from uuid import NAMESPACE_URL, UUID, uuid5

from scout.shared import AccountId

MatchId = NewType("MatchId", UUID)
MATCHING_NS = uuid5(NAMESPACE_URL, "lattice-scout/matching")


def iso_week(now: datetime) -> str:
    """``YYYY-Www`` of the ISO week of ``now``."""
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def match_id(a: AccountId, b: AccountId, week: str) -> MatchId:
    """Id of the proposal between ``a`` and ``b`` created in ISO week ``week``."""
    return MatchId(uuid5(MATCHING_NS, f"{a}:{b}:{week}"))


def idea_id(match: UUID, ord_: int) -> UUID:
    """Id of the ``ord_``-th joint idea of a match."""
    return uuid5(MATCHING_NS, f"{match}:idea:{ord_}")
