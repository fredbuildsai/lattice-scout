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


@pytest.fixture(scope="session")
def migrated_url() -> str:
    """Create the schema once per session by running Alembic from scratch."""
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    os.environ["ALEMBIC_URL"] = TEST_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest.fixture
async def db(migrated_url: str) -> AsyncIterator[Database]:
    """Database with empty mutable tables."""
    database = Database(migrated_url)
    async with database.engine.begin() as conn:
        tables = "job, audit_log, account, app_session, analytics_event, llm_call, external_call"
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield database
    await database.dispose()
