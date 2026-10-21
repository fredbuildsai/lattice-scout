"""account erasure: detachable match proposals, subject-hash index on the audit log

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE match_proposal ALTER COLUMN a_id DROP NOT NULL;
        ALTER TABLE match_proposal ALTER COLUMN b_id DROP NOT NULL;
        ALTER TABLE match_proposal ADD CONSTRAINT match_proposal_detached_check CHECK (
            (a_id IS NOT NULL OR state IN ('declined','expired'))
            AND (b_id IS NOT NULL OR state IN ('declined','expired'))
            AND num_nonnulls(a_id, b_id) >= 1
        );
        ALTER TABLE match_proposal DROP CONSTRAINT match_proposal_close_reason_check;
        ALTER TABLE match_proposal ADD CONSTRAINT match_proposal_close_reason_check
            CHECK (close_reason IN ('declined','consent_withdrawn','account_deleted'));
        CREATE INDEX audit_log_subject_hash_idx ON audit_log ((meta->>'subject_hash'))
            WHERE action = 'account.deleted';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX audit_log_subject_hash_idx;
        DELETE FROM match_proposal WHERE a_id IS NULL OR b_id IS NULL;
        ALTER TABLE match_proposal DROP CONSTRAINT match_proposal_close_reason_check;
        UPDATE match_proposal SET close_reason = 'consent_withdrawn' WHERE close_reason = 'account_deleted';
        ALTER TABLE match_proposal ADD CONSTRAINT match_proposal_close_reason_check
            CHECK (close_reason IN ('declined','consent_withdrawn'));
        ALTER TABLE match_proposal DROP CONSTRAINT match_proposal_detached_check;
        ALTER TABLE match_proposal ALTER COLUMN a_id SET NOT NULL;
        ALTER TABLE match_proposal ALTER COLUMN b_id SET NOT NULL;
        """
    )
