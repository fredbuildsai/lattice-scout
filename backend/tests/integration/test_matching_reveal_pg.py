"""Reveal guarantees in PostgreSQL, migration 0008 (plan M5 task 6; ARCHITECTURE §4.3;
docs/design/M5-matching.md §2 "match_proposal", "joint_idea", "match_reveal_v", §8 integration (a)-(c)).

Everything is seeded with raw SQL so the tests do not depend on repository code. Assumed schema (from the design):
``match_proposal(id, a_id, b_id, field, score, score_basis, rationale, a_public, b_public, state, a_decision,
b_decision, a_decided_at, b_decided_at, declined_by, close_reason, never_similar_by, created_at, expires_at,
revealed_at, closed_at, ideas_status, updated_at)``, ``joint_idea(id, match_id, match_state default 'mutual', ord,
title, question, uses_from_a, uses_from_b, first_step, possible_output, prompt_version, model, created_at)``,
view ``match_reveal_v(match_id, viewer_id, other_id, display_name, institution, orcid, email, openalex_ids)``.
Enum-like columns hold lower-case strings (``'a'``/``'b'`` sides, ``'accept'``/``'decline'`` decisions).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from scout.adapters.persistence.db import Database

pytestmark = pytest.mark.integration


@contextmanager
def violates():
    """Expect a PostgreSQL integrity violation (SQLSTATE class 23), so a missing table cannot pass."""
    with pytest.raises(DBAPIError) as info:
        yield
    state = getattr(info.value.orig, "sqlstate", None) or getattr(info.value.orig, "pgcode", None)
    assert state is not None and state.startswith("23"), f"not an integrity violation: {info.value.orig!r}"


def acc(n: int) -> UUID:
    return UUID(int=n)


async def run(db: Database, sql: str, **params):
    async with db.engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return result.all() if result.returns_rows else []


async def seed_people(db: Database, n: int) -> None:
    """Accounts 1..n with a researcher, an institution and one OpenAlex id each."""
    async with db.engine.begin() as conn:
        for i in range(1, n + 1):
            await conn.execute(
                text(
                    "INSERT INTO account (id, email, consent_matching, consent_vault_signals, launch_field, role,"
                    " created_at, updated_at) VALUES (:id, :e, true, false, 'bio-based-materials', 'user',"
                    " now(), now())"
                ),
                {"id": acc(i), "e": f"person{i}@example.edu"},
            )
            rid = uuid4()
            await conn.execute(
                text(
                    "INSERT INTO researcher (id, account_id, display_name, institution)"
                    " VALUES (:r, :a, :n, 'Example University')"
                ),
                {"r": rid, "a": acc(i), "n": f"Person {i} (sample)"},
            )
            await conn.execute(
                text(
                    "INSERT INTO researcher_openalex_id (id, researcher_id, openalex_author_id, basis, position)"
                    " VALUES (gen_random_uuid(), :r, :o, 'primary', 0)"
                ),
                {"r": rid, "o": f"A{i:04d}"},
            )


STATE_COLUMNS: dict[str, dict[str, object]] = {
    "proposed": {},
    "a_accepted": {"a_decision": "accept"},
    "b_accepted": {"b_decision": "accept"},
    "mutual": {"a_decision": "accept", "b_decision": "accept", "revealed": True},
    "declined": {"b_decision": "decline", "declined_by": "b", "close_reason": "declined"},
    "expired": {},
}


async def seed_match(db: Database, a: int, b: int, state: str, **override) -> UUID:
    """Insert a match_proposal directly. ``override`` replaces any column value (state-derived defaults)."""
    spec = dict(STATE_COLUMNS.get(state, {}))
    cols: dict[str, object] = {
        "id": uuid4(),
        "a_id": acc(a),
        "b_id": acc(b),
        "state": state,
        "a_decision": spec.get("a_decision"),
        "b_decision": spec.get("b_decision"),
        "declined_by": spec.get("declined_by"),
        "close_reason": spec.get("close_reason"),
        "revealed_at": datetime.now(UTC) if spec.get("revealed") else None,
    }
    cols.update(override)
    async with db.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO match_proposal (id, a_id, b_id, field, score, score_basis, rationale, a_public,"
                " b_public, state, a_decision, b_decision, declined_by, close_reason, created_at, expires_at,"
                " revealed_at, ideas_status, updated_at) VALUES (:id, :a_id, :b_id, 'bio-based-materials', 0.5,"
                " '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, :state, :a_decision, :b_decision,"
                " :declined_by, :close_reason, now(), now() + interval '14 days', :revealed_at, 'none', now())"
            ),
            cols,
        )
    return cols["id"]  # type: ignore[return-value]


async def seed_idea(db: Database, match: UUID, ord_: int = 0, match_state: str | None = None) -> None:
    """Insert a joint_idea; ``match_state`` forges the denormalised state column when given."""
    params: dict[str, object] = {"i": uuid4(), "m": match, "o": ord_, "ms": match_state}
    sql = (
        "INSERT INTO joint_idea (id, match_id, ord, title, question, uses_from_a, uses_from_b, first_step,"
        " possible_output, prompt_version, model, created_at{col}) VALUES (:i, :m, :o, 't', 'q', '[]'::jsonb,"
        " '[]'::jsonb, 'step', 'output', '1.1.0', 'fake', now(){val})"
    )
    sql = sql.format(col=", match_state" if match_state else "", val=", :ms" if match_state else "")
    if not match_state:
        params.pop("ms")
    async with db.engine.begin() as conn:
        await conn.execute(text(sql), params)


# ------------------------------------------------------------------ match_reveal_v


async def test_reveal_view_returns_rows_only_for_mutual_matches_in_both_directions(db: Database) -> None:
    await seed_people(db, 12)
    ids = {}
    for k, state in enumerate(STATE_COLUMNS):
        ids[state] = await seed_match(db, 2 * k + 1, 2 * k + 2, state)
    rows = await run(
        db, "SELECT match_id, viewer_id, other_id, display_name, email, openalex_ids FROM match_reveal_v"
    )
    assert {r[0] for r in rows} == {ids["mutual"]}
    mutual_a, mutual_b = acc(2 * 3 + 1), acc(2 * 3 + 2)  # k == 3 for 'mutual'
    by_viewer = {r[1]: r for r in rows}
    assert set(by_viewer) == {mutual_a, mutual_b}
    assert by_viewer[mutual_a][2] == mutual_b and by_viewer[mutual_b][2] == mutual_a
    assert by_viewer[mutual_a][3] == "Person 8 (sample)" and by_viewer[mutual_a][4] == "person8@example.edu"
    assert list(by_viewer[mutual_b][5]) == ["A0007"]


@pytest.mark.parametrize("state", ["proposed", "a_accepted", "b_accepted", "declined", "expired"])
async def test_reveal_view_is_empty_for_every_non_mutual_state(db: Database, state: str) -> None:
    await seed_people(db, 2)
    await seed_match(db, 1, 2, state)
    assert await run(db, "SELECT 1 FROM match_reveal_v") == []


async def test_reveal_view_hides_soft_deleted_accounts_and_cascades_on_hard_delete(db: Database) -> None:
    await seed_people(db, 2)
    match = await seed_match(db, 1, 2, "mutual")
    assert len(await run(db, "SELECT 1 FROM match_reveal_v")) == 2
    await run(db, "UPDATE account SET deleted_at = now() WHERE id = :a", a=acc(2))
    rows = await run(db, "SELECT viewer_id, other_id FROM match_reveal_v")
    assert [tuple(r) for r in rows] == [(acc(2), acc(1))]
    await run(db, "DELETE FROM account WHERE id = :a", a=acc(2))
    assert await run(db, "SELECT 1 FROM match_reveal_v") == []
    assert await run(db, "SELECT 1 FROM match_proposal WHERE id = :m", m=match) == []


# ------------------------------------------------------------------ CHECK constraints


@pytest.mark.parametrize(
    "label,state,a,b,override",
    [
        ("a_id must be less than b_id", "proposed", 2, 1, {}),
        ("a_id equals b_id", "proposed", 1, 1, {}),
        ("mutual without revealed_at", "mutual", 1, 2, {"revealed_at": None}),
        (
            "revealed_at on a non-mutual match",
            "proposed",
            1,
            2,
            {"revealed_at": datetime(2026, 10, 22, tzinfo=UTC)},
        ),
        ("mutual needs both accepts", "mutual", 1, 2, {"b_decision": None}),
        ("mutual with a decline", "mutual", 1, 2, {"b_decision": "decline"}),
        ("declined needs declined_by", "declined", 1, 2, {"declined_by": None}),
        ("unknown state", "withdrawn", 1, 2, {}),
        ("unknown decision", "proposed", 1, 2, {"a_decision": "maybe"}),
    ],
)
async def test_check_constraints_reject_invalid_rows(db: Database, label, state, a, b, override) -> None:
    await seed_people(db, 2)
    with violates():
        await seed_match(db, a, b, state, **override)
    assert await run(db, "SELECT 1 FROM match_proposal") == [], label


# ------------------------------------------------------------------ partial unique index


async def test_only_one_active_proposal_per_pair(db: Database) -> None:
    await seed_people(db, 3)
    await seed_match(db, 1, 2, "proposed")
    for state in ("proposed", "a_accepted", "b_accepted"):
        with violates():
            await seed_match(db, 1, 2, state)
    await seed_match(db, 1, 3, "proposed")  # another pair is fine


async def test_mutual_pair_blocks_a_new_proposal_but_closed_ones_do_not(db: Database) -> None:
    await seed_people(db, 2)
    await seed_match(db, 1, 2, "declined")
    await seed_match(db, 1, 2, "expired")
    await seed_match(db, 1, 2, "mutual")
    with violates():
        await seed_match(db, 1, 2, "proposed")
    assert len(await run(db, "SELECT 1 FROM match_proposal")) == 3


# ------------------------------------------------------------------ joint_idea guarantees


async def test_idea_can_be_stored_for_a_mutual_match(db: Database) -> None:
    await seed_people(db, 2)
    match = await seed_match(db, 1, 2, "mutual")
    await seed_idea(db, match, 0)
    await seed_idea(db, match, 2)
    assert len(await run(db, "SELECT 1 FROM joint_idea WHERE match_id = :m", m=match)) == 2


@pytest.mark.parametrize("state", ["proposed", "a_accepted", "b_accepted", "declined", "expired"])
async def test_idea_cannot_be_inserted_for_a_non_mutual_match(db: Database, state: str) -> None:
    await seed_people(db, 2)
    match = await seed_match(db, 1, 2, state)
    with violates():
        await seed_idea(db, match, 0)
    with violates():  # forging the state column does not help
        await seed_idea(db, match, 0, match_state=state)
    assert await run(db, "SELECT 1 FROM joint_idea") == []


async def test_mutual_match_with_ideas_cannot_change_state(db: Database) -> None:
    await seed_people(db, 2)
    match = await seed_match(db, 1, 2, "mutual")
    await seed_idea(db, match, 0)
    with violates():
        await run(
            db,
            "UPDATE match_proposal SET state = 'declined', revealed_at = NULL, declined_by = 'a',"
            " close_reason = 'declined' WHERE id = :m",
            m=match,
        )
    assert await run(db, "SELECT state FROM match_proposal WHERE id = :m", m=match) == [("mutual",)]


async def test_idea_ord_is_bounded_and_unique_per_match(db: Database) -> None:
    await seed_people(db, 2)
    match = await seed_match(db, 1, 2, "mutual")
    await seed_idea(db, match, 0)
    with violates():
        await seed_idea(db, match, 0)
    with violates():
        await seed_idea(db, match, 3)


# ------------------------------------------------------------------ cascade


async def test_account_delete_cascades_to_matches_and_ideas(db: Database) -> None:
    await seed_people(db, 4)
    gone = await seed_match(db, 1, 2, "mutual")
    kept = await seed_match(db, 3, 4, "mutual")
    await seed_idea(db, gone, 0)
    await seed_idea(db, kept, 0)
    await run(db, "DELETE FROM account WHERE id = :a", a=acc(1))
    assert await run(db, "SELECT 1 FROM match_proposal WHERE id = :m", m=gone) == []
    assert await run(db, "SELECT 1 FROM joint_idea WHERE match_id = :m", m=gone) == []
    assert len(await run(db, "SELECT 1 FROM joint_idea WHERE match_id = :m", m=kept)) == 1
