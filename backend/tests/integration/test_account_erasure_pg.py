"""M6 slice A on PostgreSQL 16: account deletion leaves no row and no file for the user (plan M6 acceptance:
"A test scans all tables and the bucket to prove it"; ARCHITECTURE sections 4.3, 7, 10;
docs/design/M6-deletion.md sections 0, 4 "Scan test", 8 "Risks and traps"; decisions 88, 90-92).

Flow: two users sign in through the real API (accounts A and B), ``erasure_seed.seed_world`` writes a needle row
for A in every table (plus data of the counterparts B, C and D), ``POST /api/v1/me/delete`` is called, the worker
drains the queue with the real handlers, the clock advances 15 minutes and the worker drains again (pass 2).

* ``test_every_base_table_holds_a_needle_before_deletion`` is the coverage guard: a new table that the seed (and
  therefore the deletion) does not know about fails here.
* ``test_account_deletion_scan`` walks ``information_schema`` (every base table of every schema, every column
  except ``bytea``) with no allow-list and looks for A's ids, e-mail, name and ORCID; it also lists the bucket.
* The remaining tests assert the decline-secrecy, audit, observability, stale-token and auth-admin guarantees.
* ``test_foreign_keys_to_user_roots_cascade_or_null`` is the FK guard of the design note.

The auth admin is observed by patching ``DevAuthAdmin.delete_user`` (``scout.identity.adapters.auth_admin``,
the adapter used with ``AUTH_MODE=dev``), the stand-in for the design's ``FakeAuthAdmin.deleted == [A]``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from tests.conftest import TEST_URL
from tests.integration.erasure_seed import (
    A_EMAIL,
    B_EMAIL,
    Seeded,
    pair,
    seed_world,
    utcnow,
)

from scout.adapters.storage.local import LocalFileStorage
from scout.api.app import create_app
from scout.bootstrap import build_container
from scout.settings import Settings
from scout.shared import FixedClock
from scout.worker.loop import build_handlers, run_once

pytestmark = pytest.mark.integration

NO_NEEDLE_TABLES = {"alembic_version", "openalex_cache", "joint_idea"}
SECOND_PASS = timedelta(minutes=15)


@dataclass
class World:
    client: TestClient
    clock: FixedClock
    settings: Settings
    engine: Engine
    s: Seeded
    storage_dir: Path
    headers_a: dict[str, str]
    headers_b: dict[str, str]
    auth_deleted: list[UUID] = field(default_factory=list)
    before: dict[str, Any] = field(default_factory=dict)

    def sql(self, q: str, **p: Any) -> list[tuple[Any, ...]]:
        with self.engine.connect() as conn:
            return [tuple(r) for r in conn.execute(text(q), p)]

    def drain(self) -> int:
        """Process every due job with a separate container (own event loop) on the shared fixed clock."""

        async def go() -> int:
            c = build_container(self.settings, clock=self.clock)
            handlers = build_handlers(c)
            n = 0
            while await run_once(c.job_queue, handlers, "erasure-test"):
                n += 1
            await c.db.dispose()
            return n

        return asyncio.run(go())

    def login(self, email: str) -> dict[str, str]:
        r = self.client.post("/api/v1/dev/login", json={"email": email})
        assert r.status_code == 200
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    def cards(self, headers: dict[str, str]) -> dict[str, dict[str, Any]]:
        r = self.client.get("/api/v1/matches", headers=headers)
        assert r.status_code == 200
        return {i["id"]: i for i in r.json()["items"]}


@pytest.fixture
def world(db: object, tmp_path: Path) -> Iterator[World]:
    start = utcnow().replace(microsecond=0)
    clock = FixedClock(start)
    settings = Settings(
        _env_file=None, app_env="test", database_url=TEST_URL, local_storage_dir=tmp_path / "storage"
    )
    engine = create_engine(TEST_URL)
    with TestClient(create_app(build_container(settings, clock=clock))) as client:
        w = World.__new__(World)
        w.client, w.clock, w.settings, w.engine = client, clock, settings, engine
        w.storage_dir, w.auth_deleted, w.before = tmp_path / "storage", [], {}
        w.headers_a, w.headers_b = w.login(A_EMAIL), w.login(B_EMAIL)
        a = UUID(client.get("/api/v1/me", headers=w.headers_a).json()["account_id"])
        b = UUID(client.get("/api/v1/me", headers=w.headers_b).json()["account_id"])
        w.s = Seeded(a=a, b=b, c=uuid4(), d=uuid4(), start=start)
        with engine.begin() as conn:
            seed_world(conn, w.s, w.storage_dir)
        yield w
    engine.dispose()


@pytest.fixture
def deleted(world: World, monkeypatch: pytest.MonkeyPatch) -> World:
    """``world`` after A requested deletion and both passes of the worker ran."""

    async def record(_self: object, user_id: UUID) -> None:
        world.auth_deleted.append(user_id)

    monkeypatch.setattr("scout.identity.adapters.auth_admin.DevAuthAdmin.delete_user", record)
    world.before = {
        "llm": world.sql("SELECT count(*), sum(cost_usd) FROM llm_call")[0],
        "ext": world.sql("SELECT count(*), sum(cost_usd) FROM external_call")[0],
        "b_card": world.cards(world.headers_b)[str(world.s.pending)]["status"],
        "b_rows": b_snapshot(world),
    }
    r = world.client.post(
        "/api/v1/me/delete", json={"confirm_email": f"  {A_EMAIL.upper()} "}, headers=world.headers_a
    )
    assert r.status_code == 202 and r.json() == {"status": "deleting"}
    assert world.drain() >= 1  # pass 1
    world.clock.advance(SECOND_PASS)
    assert world.drain() >= 1  # pass 2
    return world


# ------------------------------------------------------------------ the scan


def base_tables(conn: Any) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in conn.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE'"
                " AND table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast') ORDER BY 1, 2"
            )
        )
    ]


def partition_children(conn: Any) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            text("SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid")
        )
    }


def scan(conn: Any, needles: list[str]) -> dict[str, int]:
    """Rows per table (``schema.table``) whose text form of any non-bytea column contains a needle."""
    patterns = [f"%{n}%" for n in needles]
    found: dict[str, int] = {}
    for schema, table in base_tables(conn):
        cols = [
            r[0]
            for r in conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = :s AND table_name = :t"
                    " AND data_type <> 'bytea' ORDER BY ordinal_position"
                ),
                {"s": schema, "t": table},
            )
        ]
        if not cols:
            continue
        cond = " OR ".join(f'"{c}"::text ILIKE ANY(CAST(:p AS text[]))' for c in cols)
        sql = f'SELECT count(*) FROM "{schema}"."{table}" WHERE {cond}'  # noqa: S608
        n = conn.execute(text(sql), {"p": patterns}).scalar_one()
        found[f"{schema}.{table}"] = n
    return found


def b_snapshot(w: World) -> dict[str, Any]:
    """Counts of B's own rows (they must not change when A is deleted)."""
    b, bs = w.s.b, w.s.b_session
    return {
        "account": w.sql("SELECT count(*) FROM account WHERE id = :b", b=b),
        "researcher": w.sql("SELECT count(*) FROM researcher WHERE account_id = :b", b=b),
        "document": w.sql("SELECT count(*) FROM document WHERE owner_id = :b", b=b),
        "run": w.sql("SELECT count(*) FROM analysis_run WHERE owner_id = :b", b=b),
        "session": w.sql("SELECT count(*) FROM app_session WHERE id = :s", s=bs),
        "events": w.sql("SELECT count(*) FROM analytics_event WHERE session_id = :s", s=bs),
        "llm": w.sql("SELECT count(*) FROM llm_call WHERE account_id = :b AND session_id = :s", b=b, s=bs),
        "ext": w.sql(
            "SELECT count(*) FROM external_call WHERE account_id = :b AND run_id = :r", b=b, r=w.s.b_run
        ),
        "audit": w.sql(
            "SELECT count(*) FROM audit_log WHERE actor_id = :b AND action = 'consent.changed'", b=b
        ),
        "jobs": w.sql(
            "SELECT count(*) FROM job WHERE payload ->> 'researcher_id' = :r", r=str(w.s.b_researcher)
        ),
        "feedback_c": w.sql("SELECT count(*) FROM feedback WHERE author_id = :c", c=w.s.c),
    }


def test_every_base_table_holds_a_needle_before_deletion(world: World) -> None:
    """Coverage guard: a table without a needle row is a table the deletion is not tested against."""
    with world.engine.connect() as conn:
        counts, children = scan(conn, world.s.scan_needles), partition_children(conn)
    missing = sorted(
        t for t, n in counts.items() if n == 0 and t.split(".", 1)[1] not in NO_NEEDLE_TABLES | children
    )
    assert missing == [], f"tables with no needle row (extend erasure_seed.py): {missing}"


def test_account_deletion_scan(deleted: World) -> None:
    """No table holds any of A's ids, e-mail, display name or ORCID, and the bucket has none of A's objects."""
    with deleted.engine.connect() as conn:
        leftovers = {t: n for t, n in scan(conn, deleted.s.scan_needles).items() if n}
    assert leftovers == {}, f"rows still mentioning the deleted user: {leftovers}"

    store = LocalFileStorage(deleted.storage_dir)
    keys = asyncio.run(store.list_prefix("vault"))
    assert not [k for k in keys if str(deleted.s.a) in k], keys
    assert keys == [f"vault/{deleted.s.b}/{deleted.s.b_doc}"]  # B's object is still there
    assert deleted.sql("SELECT count(*) FROM account WHERE id = :a", a=deleted.s.a) == [(0,)]


def test_foreign_keys_to_user_roots_cascade_or_null(db: object) -> None:
    """Every FK to account, researcher, document or analysis_run is ON DELETE CASCADE or SET NULL."""
    engine = create_engine(TEST_URL)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conrelid::regclass::text, conname, confrelid::regclass::text, confdeltype FROM pg_constraint"
                " WHERE contype = 'f' AND confrelid IN ('account'::regclass, 'researcher'::regclass,"
                " 'document'::regclass, 'analysis_run'::regclass)"
            )
        ).all()
    engine.dispose()
    assert rows, "no foreign keys found: the query is broken"
    bad = [(r[0], r[1], r[2]) for r in rows if r[3] not in ("c", "n")]
    assert bad == [], f"foreign keys that would block deleting a user: {bad}"


# ------------------------------------------------------------------ explicit guarantees


def test_mutual_proposal_and_its_ideas_are_gone_but_the_counterparts_rating_stays(deleted: World) -> None:
    s = deleted
    assert s.sql("SELECT count(*) FROM match_proposal WHERE id = :m", m=s.s.mutual) == [(0,)]
    assert s.sql("SELECT count(*) FROM joint_idea WHERE match_id = :m", m=s.s.mutual) == [(0,)]
    rating = s.sql(
        "SELECT stars, comment FROM feedback WHERE author_id = :c AND target_id = :m", c=s.s.c, m=s.s.mutual
    )
    assert rating == [(5, "Great match")]


def test_pending_proposal_is_detached_not_deleted_and_its_perspective_is_empty(deleted: World) -> None:
    s = deleted.s
    a_id, b_id = pair(s.a, s.b)
    (row,) = deleted.sql(
        "SELECT a_id, b_id, state, close_reason, declined_by, rationale FROM match_proposal WHERE id = :p",
        p=s.pending,
    )
    gone_side = "a" if a_id == s.a else "b"
    assert (row[0] is None) == (gone_side == "a") and (row[1] is None) == (gone_side == "b")
    survivor_index, survivor = (1, b_id) if gone_side == "a" else (0, a_id)
    assert row[survivor_index] == survivor and survivor != s.a
    assert row[2] == "declined" and row[3] == "account_deleted" and row[4] == gone_side
    rationale = row[5]
    assert rationale["why"] == ""
    assert rationale["you_bring" if gone_side == "a" else "they_bring"] == []
    assert all(item["source_refs"] == [] for k in ("you_bring", "they_bring") for item in rationale[k])


def test_proposal_declined_by_another_user_survives_without_a_block_on_the_deleted_account(
    deleted: World,
) -> None:
    s = deleted.s
    (row,) = deleted.sql(
        "SELECT a_id, b_id, state, close_reason FROM match_proposal WHERE id = :p", p=s.declined
    )
    assert (
        row[2] == "declined" and row[3] == "declined" and s.d in (row[0], row[1]) and None in (row[0], row[1])
    )
    assert deleted.sql("SELECT count(*) FROM match_block") == [(0,)]


def test_counterpart_sees_the_same_card_status_then_expired(deleted: World) -> None:
    """Decline secrecy (M5 I3): what B sees after A's deletion is what B saw before, and then 'expired'."""
    s = deleted
    pid = str(s.s.pending)
    card = s.cards(s.headers_b)[pid]
    assert card["status"] == s.before["b_card"]
    assert card["status"] in ("awaiting_you", "waiting_on_them")
    s.clock.set(s.s.start + timedelta(days=15))
    fresh_b = s.login(B_EMAIL)  # the 1 hour dev token expired with the clock jump
    assert s.cards(fresh_b)[pid]["status"] == "expired"


def test_exactly_one_pii_free_tombstone_remains_in_the_audit_log(deleted: World) -> None:
    from scout.identity.domain.erasure import subject_hash

    key = deleted.settings.audit_hash_key.get_secret_value()
    rows = deleted.sql("SELECT actor_id, target, meta FROM audit_log WHERE action = 'account.deleted'")
    assert len(rows) == 1
    actor, target, meta = rows[0]
    assert actor is None and target is None and meta["subject_hash"] == subject_hash(key, deleted.s.a)
    assert A_EMAIL not in str(meta) and "example.edu" not in str(meta)
    assert deleted.sql("SELECT count(*) FROM audit_log WHERE action = 'account.deletion_requested'") == [(0,)]


def test_cost_rows_are_anonymised_not_deleted(deleted: World) -> None:
    before = deleted.before
    assert deleted.sql("SELECT count(*), sum(cost_usd) FROM llm_call")[0] == before["llm"]
    assert deleted.sql("SELECT count(*), sum(cost_usd) FROM external_call")[0] == before["ext"]
    s = deleted.s
    rows = deleted.sql(
        "SELECT account_id, session_id, run_id, trace_id FROM llm_call WHERE id = ANY(:ids)",
        ids=s.llm_calls[:2],
    )
    assert len(rows) == 2 and all(r == (None, None, None, None) for r in rows)
    ext = deleted.sql(
        "SELECT account_id, session_id, run_id, trace_id FROM external_call WHERE id = :i",
        i=s.external_calls[0],
    )
    assert ext == [(None, None, None, None)]
    assert deleted.sql("SELECT count(*) FROM analytics_event WHERE session_id = :s", s=s.session) == [(0,)]


def test_counterpart_data_is_untouched(deleted: World) -> None:
    assert b_snapshot(deleted) == deleted.before["b_rows"]
    assert deleted.sql("SELECT count(*) FROM job WHERE kind = 'account.delete'")[0][0] >= 2
    for _, payload in deleted.sql("SELECT status, payload::text FROM job WHERE kind = 'account.delete'"):
        assert str(deleted.s.a) not in payload and "subject_id" not in payload


def test_stale_token_cannot_recreate_the_account(deleted: World) -> None:
    r = deleted.client.get("/api/v1/me", headers=deleted.headers_a)
    assert r.status_code == 403 and r.json()["code"] == "account_deleted"
    assert deleted.sql("SELECT count(*) FROM account WHERE id = :a", a=deleted.s.a) == [(0,)]


def test_auth_user_was_deleted_exactly_for_a(deleted: World) -> None:
    assert set(deleted.auth_deleted) == {deleted.s.a}


def test_requesting_deletion_blocks_the_account_immediately(world: World) -> None:
    bad = world.client.post(
        "/api/v1/me/delete", json={"confirm_email": "someone-else@example.edu"}, headers=world.headers_a
    )
    assert bad.status_code == 422 and bad.json()["code"] == "confirmation_mismatch"
    assert world.client.get("/api/v1/me", headers=world.headers_a).status_code == 200
    ok = world.client.post("/api/v1/me/delete", json={"confirm_email": A_EMAIL}, headers=world.headers_a)
    assert ok.status_code == 202
    blocked = world.client.get("/api/v1/me", headers=world.headers_a)
    assert blocked.status_code == 403 and blocked.json()["code"] == "account_deleted"
    again = world.client.post("/api/v1/me/delete", json={"confirm_email": A_EMAIL}, headers=world.headers_a)
    assert again.status_code == 202  # idempotent
    row = world.sql(
        "SELECT deleted_at, consent_matching, consent_vault_signals FROM account WHERE id = :a", a=world.s.a
    )
    assert row[0][0] is not None and row[0][1:] == (False, False)
