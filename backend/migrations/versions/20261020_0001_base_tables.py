"""base tables: job, account, audit_log

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("now()")


def _ts(name: str, **kw: object) -> sa.Column[object]:
    return sa.Column(name, pg.TIMESTAMP(timezone=True), **kw)  # type: ignore[arg-type]


def upgrade() -> None:
    op.create_table(
        "account",
        sa.Column("id", pg.UUID, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("consent_matching", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("consent_vault_signals", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("launch_field", sa.Text, nullable=False, server_default="bio-based-materials"),
        sa.Column("role", sa.Text, nullable=False, server_default="user"),
        _ts("deleted_at", nullable=True),
        _ts("created_at", nullable=False, server_default=NOW),
        _ts("updated_at", nullable=False, server_default=NOW),
    )
    op.create_table(
        "job",
        sa.Column("id", pg.UUID, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("payload", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        _ts("run_at", nullable=False, server_default=NOW),
        sa.Column("locked_by", sa.Text),
        _ts("locked_at", nullable=True),
        sa.Column("last_error", sa.Text),
        sa.Column("result", pg.JSONB),
        sa.Column("dedupe_key", sa.Text),
        _ts("created_at", nullable=False, server_default=NOW),
        _ts("updated_at", nullable=False, server_default=NOW),
        sa.CheckConstraint("status in ('queued','running','succeeded','dead')", name="job_status_check"),
    )
    op.create_index("job_due_idx", "job", ["run_at"], postgresql_where=sa.text("status = 'queued'"))
    op.create_index(
        "job_dedupe_idx",
        "job",
        ["kind", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", pg.UUID, primary_key=True),
        sa.Column("actor_id", pg.UUID),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target", sa.Text),
        _ts("at", nullable=False, server_default=NOW),
        sa.Column("meta", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False, server_default=NOW),
        _ts("updated_at", nullable=False, server_default=NOW),
    )
    op.create_index("audit_log_actor_idx", "audit_log", ["actor_id", "at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("job")
    op.drop_table("account")
