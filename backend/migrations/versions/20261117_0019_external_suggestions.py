"""external suggestions: researchers not on Lattice Scout, found in OpenAlex (DECISIONS 132)

`match_external_suggestion` keeps, per account, public OpenAlex facts about a researcher who is not a member (name,
institution, h-index, shared topics, the co-authors who link you). Nobody outside the account sees these rows; the
people in them are never contacted and never enter match proposals. `match_external_refresh` logs searches for the daily
limit. Both are removed with the account (erasure deletes them explicitly; the foreign keys cascade as a backstop).
RLS on, no policies, like every table (0013).

Revision ID: 0019
Revises: 0018
"""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE match_external_suggestion (
            id uuid PRIMARY KEY,
            account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            openalex_id text NOT NULL,
            name text NOT NULL,
            institution text,
            score real NOT NULL CHECK (score >= 0 AND score <= 1),
            topic_overlap real NOT NULL CHECK (topic_overlap >= 0 AND topic_overlap <= 1),
            formula text NOT NULL DEFAULT 'ext1',
            layer text NOT NULL CHECK (layer IN ('in_network','in_field')),
            vias text[] NOT NULL DEFAULT '{}',
            shared_topics text[] NOT NULL DEFAULT '{}',
            h_index integer NOT NULL DEFAULT 0,
            citations bigint NOT NULL DEFAULT 0,
            works_count integer NOT NULL DEFAULT 0,
            recent_works integer NOT NULL DEFAULT 0,
            state text NOT NULL DEFAULT 'active' CHECK (state IN ('active','saved','dismissed')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (account_id, openalex_id)
        );
        CREATE TABLE match_external_refresh (
            id bigserial PRIMARY KEY,
            account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX match_external_refresh_account_idx ON match_external_refresh (account_id, created_at);
        ALTER TABLE match_external_suggestion ENABLE ROW LEVEL SECURITY;
        ALTER TABLE match_external_refresh ENABLE ROW LEVEL SECURITY;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE who text;
        BEGIN
          FOREACH who IN ARRAY ARRAY['anon','authenticated'] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = who) THEN
              EXECUTE format('REVOKE ALL ON match_external_suggestion, match_external_refresh FROM %I', who);
              EXECUTE format('REVOKE ALL ON SEQUENCE match_external_refresh_id_seq FROM %I', who);
            END IF;
          END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS match_external_refresh; DROP TABLE IF EXISTS match_external_suggestion;")
