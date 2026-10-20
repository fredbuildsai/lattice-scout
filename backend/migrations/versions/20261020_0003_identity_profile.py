"""identity + profile: researcher, researcher_openalex_id, claimed_work, researcher_topic, researcher_method, openalex_cache

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE researcher (
            id uuid PRIMARY KEY,
            account_id uuid NOT NULL UNIQUE REFERENCES account(id) ON DELETE CASCADE,
            display_name text NOT NULL,
            orcid text, institution text, country text, career_stage text,
            verified_level text NOT NULL DEFAULT 'self_claimed'
                CHECK (verified_level IN ('self_claimed','orcid_matched','contested')),
            import_status text NOT NULL DEFAULT 'queued'
                CHECK (import_status IN ('queued','importing','done','failed')),
            import_generation integer NOT NULL DEFAULT 0,
            import_error text,
            import_requested_at timestamptz,
            imported_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE researcher_openalex_id (
            id uuid PRIMARY KEY,
            researcher_id uuid NOT NULL REFERENCES researcher(id) ON DELETE CASCADE,
            openalex_author_id text NOT NULL,
            merged boolean NOT NULL DEFAULT false,
            basis text NOT NULL CHECK (basis IN ('primary','orcid','institution_name','manual')),
            display_name text NOT NULL DEFAULT '',
            orcid text,
            works_count integer NOT NULL DEFAULT 0,
            imported_generation integer,
            position integer NOT NULL DEFAULT 0,
            UNIQUE (researcher_id, openalex_author_id)
        );
        CREATE INDEX researcher_openalex_id_author_idx ON researcher_openalex_id (openalex_author_id);

        CREATE TABLE claimed_work (
            id uuid PRIMARY KEY,
            researcher_id uuid NOT NULL REFERENCES researcher(id) ON DELETE CASCADE,
            openalex_work_id text NOT NULL,
            doi text, title text NOT NULL, year integer, cited_by integer NOT NULL DEFAULT 0,
            type text, abstract text,
            topics jsonb NOT NULL DEFAULT '[]'::jsonb,
            source_author_ids text[] NOT NULL DEFAULT '{}',
            included boolean NOT NULL DEFAULT true,
            included_by_user boolean NOT NULL DEFAULT false,
            uncertain_reason text CHECK (uncertain_reason IN ('non_article_type','name_mismatch')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (researcher_id, openalex_work_id)
        );
        CREATE INDEX claimed_work_included_idx ON claimed_work (researcher_id, included);

        CREATE TABLE researcher_topic (
            id uuid PRIMARY KEY,
            researcher_id uuid NOT NULL REFERENCES researcher(id) ON DELETE CASCADE,
            openalex_topic_id text NOT NULL,
            name text, subfield text,
            weight real NOT NULL CHECK (weight >= 0 AND weight <= 1),
            UNIQUE (researcher_id, openalex_topic_id)
        );

        CREATE TABLE researcher_method (
            id uuid PRIMARY KEY,
            researcher_id uuid NOT NULL REFERENCES researcher(id) ON DELETE CASCADE,
            method_term text NOT NULL,
            weight real NOT NULL CHECK (weight >= 0 AND weight <= 1),
            source text NOT NULL CHECK (source IN ('abstract','vault')),
            UNIQUE (researcher_id, method_term, source)
        );

        CREATE TABLE openalex_cache (
            key text PRIMARY KEY,
            response jsonb NOT NULL,
            fetched_at timestamptz NOT NULL
        );
        CREATE INDEX openalex_cache_fetched_idx ON openalex_cache (fetched_at);
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS openalex_cache, researcher_method, researcher_topic, claimed_work, "
        "researcher_openalex_id, researcher CASCADE"
    )
