"""Security guards on PostgreSQL (M6 slice E): row level security on every table."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from tests.conftest import TEST_URL

pytestmark = pytest.mark.integration


def test_every_table_has_row_level_security_and_no_policy(db: object) -> None:
    """A new table without RLS fails here. Partition parents and children are all covered."""
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT n.nspname || '.' || c.relname, c.relrowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('public','analytics') AND c.relkind IN ('r','p') AND c.relname <> 'alembic_version'"
            )
        ).all()
        policies = conn.execute(
            text("SELECT count(*) FROM pg_policies WHERE schemaname IN ('public','analytics')")
        ).scalar()
    eng.dispose()
    assert rows, "no tables found"
    assert [r[0] for r in rows if not r[1]] == []
    assert policies == 0, (
        "policies would open tables to PostgREST roles; add them deliberately and update this test"
    )


def test_new_partitions_get_rls_and_expired_ones_are_dropped(db: object) -> None:
    import asyncio
    from datetime import UTC, datetime

    from scout.adapters.observability.partitions import drop_expired_partitions, ensure_partitions
    from scout.adapters.persistence.db import Database
    from scout.shared import FixedClock

    async def go() -> tuple[int, int]:
        d = Database(TEST_URL)
        try:
            old = FixedClock(datetime(2025, 1, 15, tzinfo=UTC))
            await ensure_partitions(d, old)  # creates 2025-01..2025-0x partitions
            now = FixedClock(datetime(2026, 10, 15, tzinfo=UTC))
            await ensure_partitions(d, now)
            return await drop_expired_partitions(d, now, 13), await drop_expired_partitions(d, now, 13)
        finally:
            await d.dispose()

    first, second = asyncio.run(go())
    assert first >= 1 and second == 0  # idempotent
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        left = conn.execute(
            text(
                "SELECT relname FROM pg_class WHERE relname ~ '^(analytics_event|llm_call|external_call)_y2025m0[1-9]$'"
            )
        ).all()
        no_rls = conn.execute(
            text(
                "SELECT relname FROM pg_class WHERE relname ~ '_y2026m' AND relkind = 'r' AND NOT relrowsecurity"
            )
        ).all()
    eng.dispose()
    assert left == [] and no_rls == []


def test_funnel_and_ops_views_run_on_an_empty_database(db: object) -> None:
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        for view in ("v_funnel_discovery", "v_funnel_matching", "v_run_cost", "v_ops_signals"):
            conn.execute(text(f"SELECT * FROM analytics.{view}")).all()  # noqa: S608
        sig = conn.execute(
            text("SELECT dead_jobs_24h, oldest_due_job_age_s FROM analytics.v_ops_signals")
        ).one()
    eng.dispose()
    assert sig.dead_jobs_24h == 0


def test_postgrest_roles_hold_no_privilege_on_any_relation_and_views_are_security_invoker(db: object) -> None:
    """Tables, views, sequences and functions of `public` and `analytics`; the test DB grants by default like
    Supabase does (conftest), so this fails when a migration adds an object without closing the door."""
    eng = create_engine(TEST_URL)
    with eng.connect() as conn:
        rels = conn.execute(
            text(
                "SELECT c.oid, n.nspname || '.' || c.relname, c.relkind, c.reloptions FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('public','analytics') AND c.relkind IN ('r','p','v','m','S')"
            )
        ).all()
        funcs = conn.execute(
            text(
                "SELECT p.oid, n.nspname || '.' || p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname IN ('public','analytics')"
            )
        ).all()
        leaks: list[str] = []
        for role in ("anon", "authenticated"):
            for oid, name, kind, _ in rels:
                fn = "has_sequence_privilege" if kind == "S" else "has_table_privilege"
                priv = "USAGE" if kind == "S" else "SELECT"
                if conn.execute(text(f"SELECT {fn}(:r, :o, '{priv}')"), {"r": role, "o": oid}).scalar():
                    leaks.append(f"{role} {name}")
            for oid, name in funcs:
                if conn.execute(
                    text("SELECT has_function_privilege(:r, :o, 'EXECUTE')"), {"r": role, "o": oid}
                ).scalar():
                    leaks.append(f"{role} {name}()")
        # a table created now by the migrating role must not be granted by default either
        conn.execute(text("CREATE TABLE public._probe_default_grants (id int)"))
        probe = conn.execute(
            text("SELECT has_table_privilege('anon', 'public._probe_default_grants', 'SELECT')")
        ).scalar()
        conn.rollback()
    eng.dispose()
    assert rels and leaks == [], f"PostgREST roles can reach: {leaks}"
    assert probe is False, "default privileges still grant new tables to anon"
    views = [(name, opts) for _, name, kind, opts in rels if kind == "v"]
    assert views, "no views found"
    assert [n for n, o in views if not o or "security_invoker=true" not in o] == []


def test_sql_reader_counts_todays_llm_calls(db: object) -> None:
    import asyncio
    from datetime import UTC, datetime

    from scout.adapters.observability.budget import SqlSpendReader
    from scout.adapters.persistence.db import Database

    async def go() -> int:
        d = Database(TEST_URL)
        try:
            return await SqlSpendReader(d).llm_calls_since(datetime(2999, 1, 1, tzinfo=UTC))
        finally:
            await d.dispose()

    assert asyncio.run(go()) == 0
