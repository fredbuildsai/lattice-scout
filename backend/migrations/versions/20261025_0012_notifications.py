"""notifications: preferences and the delivery log

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_pref (
            account_id uuid PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
            email_runs boolean NOT NULL DEFAULT true,
            email_matches boolean NOT NULL DEFAULT true,
            email_digest boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE notification (
            id uuid PRIMARY KEY,
            account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            template text NOT NULL CHECK (template IN ('run_finished','match_proposed','match_mutual','weekly_digest')),
            ref text NOT NULL,
            sent_at timestamptz NOT NULL,
            UNIQUE (account_id, template, ref)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE notification; DROP TABLE notification_pref;")
