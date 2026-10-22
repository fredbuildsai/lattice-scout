"""feedback: 1-5 stars for matches, more reasons, stars in v_feedback_summary

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

OPP = "'spot_on','already_knew','not_feasible','wrong_field','too_vague'"
MATCH = "'methods_fit','right_seniority','shared_timeline','too_similar','already_knew_them'"


def upgrade() -> None:
    op.execute(
        f"""
        DROP VIEW analytics.v_feedback_summary;
        ALTER TABLE feedback DROP CONSTRAINT feedback_reasons_check;
        ALTER TABLE feedback DROP CONSTRAINT feedback_rating_check;
        ALTER TABLE feedback ALTER COLUMN rating DROP NOT NULL;
        ALTER TABLE feedback ADD COLUMN stars smallint CHECK (stars >= 1 AND stars <= 5);
        ALTER TABLE feedback ADD CONSTRAINT feedback_rating_check CHECK (rating IN ('up','down'));
        ALTER TABLE feedback ADD CONSTRAINT feedback_reasons_check CHECK (reasons <@ ARRAY[{OPP},{MATCH}]);
        ALTER TABLE feedback ADD CONSTRAINT feedback_kind_check CHECK (
            (target_type = 'opportunity' AND rating IS NOT NULL AND stars IS NULL)
            OR (target_type = 'match' AND stars IS NOT NULL AND rating IS NULL));
        CREATE VIEW analytics.v_feedback_summary AS
        SELECT date_trunc('day', f.updated_at)::date AS day, f.target_type, f.rating, r.reason,
               count(*) AS n, count(DISTINCT f.author_id) AS authors, avg(f.stars) AS avg_stars
        FROM feedback f
        LEFT JOIN LATERAL unnest(f.reasons) AS r(reason) ON true
        GROUP BY 1, 2, 3, 4;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP VIEW analytics.v_feedback_summary;
        DELETE FROM feedback WHERE target_type = 'match';
        ALTER TABLE feedback DROP CONSTRAINT feedback_kind_check, DROP CONSTRAINT feedback_reasons_check,
            DROP CONSTRAINT feedback_rating_check, DROP COLUMN stars;
        ALTER TABLE feedback ALTER COLUMN rating SET NOT NULL;
        ALTER TABLE feedback ADD CONSTRAINT feedback_rating_check CHECK (rating IN ('up','down'));
        ALTER TABLE feedback ADD CONSTRAINT feedback_reasons_check CHECK (reasons <@ ARRAY[{OPP}]);
        CREATE VIEW analytics.v_feedback_summary AS
        SELECT date_trunc('day', f.updated_at)::date AS day, f.target_type, f.rating, r.reason,
               count(*) AS n, count(DISTINCT f.author_id) AS authors
        FROM feedback f
        LEFT JOIN LATERAL unnest(f.reasons) AS r(reason) ON true
        GROUP BY 1, 2, 3, 4;
        """
    )
