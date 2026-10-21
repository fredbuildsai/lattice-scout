"""feedback: ratings of opportunities and matches, and the admin view v_feedback_summary

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE feedback (
            id uuid PRIMARY KEY,
            author_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            target_type text NOT NULL CHECK (target_type IN ('opportunity','match')),
            target_id uuid NOT NULL,
            rating text NOT NULL CHECK (rating IN ('up','down')),
            reasons text[] NOT NULL DEFAULT '{}'
                CHECK (reasons <@ ARRAY['spot_on','already_knew','not_feasible','wrong_field','too_vague']),
            comment text CHECK (comment IS NULL OR char_length(comment) <= 1000),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX feedback_author_target_idx ON feedback (author_id, target_type, target_id);
        CREATE INDEX feedback_target_idx ON feedback (target_type, target_id);

        CREATE VIEW analytics.v_feedback_summary AS
        SELECT date_trunc('day', f.updated_at)::date AS day, f.target_type, f.rating, r.reason,
               count(*) AS n, count(DISTINCT f.author_id) AS authors
        FROM feedback f
        LEFT JOIN LATERAL unnest(f.reasons) AS r(reason) ON true
        GROUP BY 1, 2, 3, 4;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.v_feedback_summary; DROP TABLE IF EXISTS feedback")
