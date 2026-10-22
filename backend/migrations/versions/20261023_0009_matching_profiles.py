"""matching profiles: match_profile (+topics, methods), method label proposals, pool and conflict views

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE match_profile (
            account_id uuid PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
            researcher_id uuid NOT NULL REFERENCES researcher(id) ON DELETE CASCADE,
            field text NOT NULL,
            region text NOT NULL,
            career_band text NOT NULL,
            activity real NOT NULL CHECK (activity >= 0 AND activity <= 1),
            eligible boolean NOT NULL,
            topics_hash text NOT NULL,
            methods_hash text NOT NULL,
            vocabulary_version text NOT NULL,
            methods_prompt_version text NOT NULL,
            uses_vault_signals boolean NOT NULL DEFAULT false,
            computed_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE match_profile_topic (
            account_id uuid NOT NULL REFERENCES match_profile(account_id) ON DELETE CASCADE,
            topic_id text NOT NULL,
            name text NOT NULL,
            weight real NOT NULL CHECK (weight > 0 AND weight <= 1),
            PRIMARY KEY (account_id, topic_id)
        );
        CREATE INDEX match_profile_topic_topic_idx ON match_profile_topic (topic_id) INCLUDE (account_id, weight);
        CREATE TABLE match_profile_method (
            account_id uuid NOT NULL REFERENCES match_profile(account_id) ON DELETE CASCADE,
            method_id text NOT NULL,
            weight real NOT NULL CHECK (weight > 0 AND weight <= 1),
            source text NOT NULL CHECK (source IN ('abstract','vault')),
            PRIMARY KEY (account_id, method_id, source)
        );
        CREATE TABLE match_method_proposal (
            id uuid PRIMARY KEY,
            account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            label text NOT NULL,
            source_refs text[] NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX match_method_proposal_label_idx ON match_method_proposal (account_id, lower(label));

        CREATE VIEW match_pool_v AS
        SELECT p.account_id, p.field
        FROM match_profile p JOIN account a ON a.id = p.account_id
        WHERE p.eligible AND a.consent_matching AND a.deleted_at IS NULL AND a.launch_field = p.field;

        -- Pairs of accounts (x < y) that should not meet, by reason. Account ids only.
        CREATE VIEW match_pair_conflict_v AS
        SELECT DISTINCT LEAST(r1.account_id, r2.account_id) AS x, GREATEST(r1.account_id, r2.account_id) AS y,
               'coauthor'::text AS reason
        FROM claimed_work w1
        JOIN claimed_work w2 ON w2.openalex_work_id = w1.openalex_work_id AND w2.researcher_id > w1.researcher_id
        JOIN researcher r1 ON r1.id = w1.researcher_id
        JOIN researcher r2 ON r2.id = w2.researcher_id
        UNION ALL
        SELECT LEAST(r1.account_id, r2.account_id), GREATEST(r1.account_id, r2.account_id), 'same_group'
        FROM researcher r1
        JOIN researcher r2 ON r2.account_id > r1.account_id
             AND lower(btrim(r2.institution)) = lower(btrim(r1.institution))
        WHERE r1.institution IS NOT NULL AND btrim(r1.institution) <> '';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS match_pair_conflict_v, match_pool_v;
        DROP TABLE IF EXISTS match_method_proposal, match_profile_method, match_profile_topic, match_profile;
        """
    )
