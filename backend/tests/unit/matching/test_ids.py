"""Deterministic match ids (plan M5 task 4; docs/design/M5-matching.md §1 ``ids.py``, §4 step 4).

Assumed API, scout.matching.domain.ids:
* ``MATCHING_NS = uuid5(NAMESPACE_URL, "lattice-scout/matching")``
* ``iso_week(now: datetime) -> str`` formatted ``"YYYY-Www"`` from ``now.isocalendar()`` (ISO year, zero padded week)
* ``match_id(a: AccountId, b: AccountId, iso_week: str) -> MatchId`` = ``uuid5(MATCHING_NS, f"{a}:{b}:{iso_week}")``
* ``idea_id(match: MatchId, ord: int) -> UUID``, deterministic and distinct per ``ord``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from scout.matching.domain.ids import MATCHING_NS, idea_id, iso_week, match_id
from scout.shared import AccountId

A = AccountId(UUID(int=1))
B = AccountId(UUID(int=2))
C = AccountId(UUID(int=3))


def test_namespace_and_formula() -> None:
    assert uuid5(NAMESPACE_URL, "lattice-scout/matching") == MATCHING_NS
    assert match_id(A, B, "2026-W43") == uuid5(MATCHING_NS, f"{A}:{B}:2026-W43")


def test_match_id_is_deterministic_and_pair_sensitive() -> None:
    assert match_id(A, B, "2026-W43") == match_id(A, B, "2026-W43")
    assert match_id(A, B, "2026-W43") != match_id(A, C, "2026-W43")


def test_match_id_changes_with_iso_week() -> None:
    assert match_id(A, B, "2026-W43") != match_id(A, B, "2026-W44")


@pytest.mark.parametrize(
    "when,expected",
    [
        (datetime(2026, 10, 21, 12, tzinfo=UTC), "2026-W43"),
        (datetime(2026, 10, 19, 0, 0, tzinfo=UTC), "2026-W43"),  # Monday
        (datetime(2026, 10, 25, 23, 59, tzinfo=UTC), "2026-W43"),  # Sunday
        (datetime(2026, 10, 26, 0, 0, tzinfo=UTC), "2026-W44"),
        (datetime(2027, 1, 1, tzinfo=UTC), "2026-W53"),  # ISO year differs from calendar year
        (datetime(2024, 12, 30, tzinfo=UTC), "2025-W01"),
        (datetime(2026, 1, 5, tzinfo=UTC), "2026-W02"),  # zero padded
    ],
)
def test_iso_week_format(when, expected) -> None:
    assert iso_week(when) == expected


def test_same_week_same_id_next_week_new_id() -> None:
    mon = match_id(A, B, iso_week(datetime(2026, 10, 19, tzinfo=UTC)))
    sun = match_id(A, B, iso_week(datetime(2026, 10, 25, tzinfo=UTC)))
    nxt = match_id(A, B, iso_week(datetime(2026, 10, 26, tzinfo=UTC)))
    assert mon == sun != nxt


def test_idea_ids_are_deterministic_and_distinct() -> None:
    m = match_id(A, B, "2026-W43")
    ids = [idea_id(m, i) for i in range(3)]
    assert len(set(ids)) == 3
    assert ids == [idea_id(m, i) for i in range(3)]
    assert idea_id(m, 0) != idea_id(match_id(A, B, "2026-W44"), 0)
