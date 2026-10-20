"""Alembic environment. URL comes from DATABASE_URL (settings) unless overridden with -x url=..."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, pool

from scout.settings import Settings

config = context.config


def _url() -> str:
    x = context.get_x_argument(as_dictionary=True)
    return x.get("url") or os.environ.get("ALEMBIC_URL") or Settings().database_url


def run() -> None:
    """Run migrations online (or emit SQL with --sql)."""
    if context.is_offline_mode():
        context.configure(url=_url(), literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as conn:
        context.configure(connection=conn)
        with context.begin_transaction():
            context.run_migrations()


run()
