"""feedback: reason chips checked per target type, at most 5

Revision ID: 0016
Revises: 0015
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

OPP = "'spot_on','already_knew','not_feasible','wrong_field','too_vague'"
MATCH = "'methods_fit','right_seniority','shared_timeline','too_similar','already_knew_them'"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE feedback SET reasons = ARRAY(
            SELECT x FROM unnest(reasons) WITH ORDINALITY AS u(x, i)
            WHERE x = ANY(CASE WHEN target_type = 'opportunity' THEN ARRAY[{OPP}] ELSE ARRAY[{MATCH}] END)
            ORDER BY i LIMIT 5);
        ALTER TABLE feedback DROP CONSTRAINT feedback_reasons_check;
        ALTER TABLE feedback ADD CONSTRAINT feedback_reasons_check CHECK (
            cardinality(reasons) <= 5 AND (
                (target_type = 'opportunity' AND reasons <@ ARRAY[{OPP}])
                OR (target_type = 'match' AND reasons <@ ARRAY[{MATCH}])));
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE feedback DROP CONSTRAINT feedback_reasons_check;
        ALTER TABLE feedback ADD CONSTRAINT feedback_reasons_check CHECK (reasons <@ ARRAY[{OPP},{MATCH}]);
        """
    )
