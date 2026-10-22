"""document status awaiting_approved_provider (ADR 016)

Revision ID: 0017
Revises: 0016
"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

OLD = "'uploaded','extracting','extracted','analysing','ready','failed','rejected','deleting'"
NEW = "'uploaded','extracting','extracted','analysing','awaiting_approved_provider','ready','failed','rejected','deleting'"


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE document DROP CONSTRAINT document_status_check;
        ALTER TABLE document ADD CONSTRAINT document_status_check CHECK (status IN ({NEW}));
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE document SET status = 'extracted' WHERE status = 'awaiting_approved_provider';
        ALTER TABLE document DROP CONSTRAINT document_status_check;
        ALTER TABLE document ADD CONSTRAINT document_status_check CHECK (status IN ({OLD}));
        """
    )
