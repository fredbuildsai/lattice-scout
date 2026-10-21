"""vault: document, document_chunk, document_insights

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            filename text NOT NULL,
            mime text NOT NULL CHECK (mime IN ('application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
            size_bytes bigint NOT NULL CHECK (size_bytes > 0),
            sha256 char(64) NOT NULL,
            storage_key text NOT NULL,
            retention text NOT NULL CHECK (retention IN ('keep','discard')),
            kind text CHECK (kind IN ('funded_grant','declined_grant','proposal','unpublished_results',
                'raw_data_description','lab_notes','other')),
            kind_suggested text CHECK (kind_suggested IN ('funded_grant','declined_grant','proposal',
                'unpublished_results','raw_data_description','lab_notes','other')),
            kind_reason text,
            status text NOT NULL CHECK (status IN ('uploaded','extracting','extracted','analysing','ready',
                'failed','rejected','deleting')),
            failure_code text,
            pages integer,
            page_basis text CHECK (page_basis IN ('physical','page_breaks','none')),
            chunk_count integer,
            extractor text,
            original_deleted_at timestamptz,
            extracted_at timestamptz,
            ready_at timestamptz,
            deleted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX document_owner_sha_active_idx ON document (owner_id, sha256)
            WHERE status NOT IN ('failed','rejected','deleting');
        CREATE INDEX document_owner_created_idx ON document (owner_id, created_at DESC)
            WHERE status <> 'deleting';
        CREATE INDEX document_status_updated_idx ON document (status, updated_at);

        CREATE TABLE document_chunk (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES document(id) ON DELETE CASCADE,
            ord integer NOT NULL,
            label text NOT NULL,
            page_from integer NOT NULL,
            page_to integer NOT NULL CHECK (page_to >= page_from),
            char_start integer NOT NULL,
            char_end integer NOT NULL,
            text text,
            quotes jsonb NOT NULL DEFAULT '[]'::jsonb,
            summary text,
            redacted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (document_id, ord),
            CHECK (text IS NOT NULL OR redacted_at IS NOT NULL)
        );

        CREATE TABLE document_insights (
            document_id uuid PRIMARY KEY REFERENCES document(id) ON DELETE CASCADE,
            payload jsonb NOT NULL,
            verified_count integer NOT NULL DEFAULT 0,
            unverified_count integer NOT NULL DEFAULT 0,
            prompt_version text NOT NULL,
            model text NOT NULL,
            mode text NOT NULL CHECK (mode IN ('full','map_reduce')),
            slices integer NOT NULL DEFAULT 1,
            cost_usd numeric(12,8) NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS document_insights, document_chunk, document CASCADE")
