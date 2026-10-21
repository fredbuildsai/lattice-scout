"""Shared fixtures. Integration tests use the VM's PostgreSQL 16 (database ``scout_test``)."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from scout.adapters.persistence.db import Database

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://scout:scout@localhost:5432/scout_test")
BACKEND = Path(__file__).resolve().parents[1]


def _supabase_like_roles() -> None:
    """Create ``anon`` and ``authenticated`` and grant them every new object by default, as Supabase does.

    The migrations must undo this, otherwise a table or view added later is readable through PostgREST.
    """
    from sqlalchemy import create_engine

    eng = create_engine(TEST_URL, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        for role in ("anon", "authenticated"):
            conn.execute(
                text(
                    f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "  # noqa: S608
                    f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$"
                )
            )
        for schema in ("public",):  # `analytics` is created by migration 0002
            conn.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO anon, authenticated"))
            for kind in ("TABLES", "SEQUENCES", "FUNCTIONS"):
                conn.execute(
                    text(
                        f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT ALL ON {kind} TO anon, authenticated"
                    )
                )
    eng.dispose()


@pytest.fixture(scope="session")
def migrated_url() -> str:
    """Create the schema once per session by running Alembic from scratch."""
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    os.environ["ALEMBIC_URL"] = TEST_URL
    _supabase_like_roles()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture
async def db(migrated_url: str) -> AsyncIterator[Database]:
    """Database with empty mutable tables."""
    database = Database(migrated_url)
    async with database.engine.begin() as conn:
        tables = (
            "job, audit_log, account, app_session, analytics_event, llm_call, external_call, openalex_cache"
        )
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield database
    await database.dispose()
