"""matching: match_proposal, joint_idea, match_block, the reveal view and the funnel view

Revision ID: 0008
Revises: 0007

Profile tables and the candidate views arrive with the compute job (0009).
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE match_proposal (
            id uuid PRIMARY KEY,
            a_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            b_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            field text NOT NULL,
            score real NOT NULL CHECK (score >= 0 AND score <= 1),
            score_basis jsonb NOT NULL,
            rationale jsonb NOT NULL,
            a_public jsonb NOT NULL,
            b_public jsonb NOT NULL,
            state text NOT NULL
                CHECK (state IN ('proposed','a_accepted','b_accepted','mutual','declined','expired')),
            a_decision text CHECK (a_decision IN ('accept','decline')),
            b_decision text CHECK (b_decision IN ('accept','decline')),
            a_decided_at timestamptz,
            b_decided_at timestamptz,
            declined_by text CHECK (declined_by IN ('a','b')),
            close_reason text CHECK (close_reason IN ('declined','consent_withdrawn')),
            never_similar_by text CHECK (never_similar_by IN ('a','b')),
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            revealed_at timestamptz,
            closed_at timestamptz,
            ideas_status text NOT NULL DEFAULT 'none' CHECK (ideas_status IN ('none','pending','ready','failed')),
            ideas_prompt_version text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (a_id < b_id),
            CHECK ((state = 'mutual') = (revealed_at IS NOT NULL)),
            CHECK (state <> 'mutual' OR (a_decision IS NOT DISTINCT FROM 'accept' AND b_decision IS NOT DISTINCT FROM 'accept')),
            CHECK (state <> 'declined' OR declined_by IS NOT NULL),
            UNIQUE (id, state)
        );
        CREATE UNIQUE INDEX match_proposal_active_pair_idx ON match_proposal (a_id, b_id)
            WHERE state IN ('proposed','a_accepted','b_accepted','mutual');
        CREATE INDEX match_proposal_a_idx ON match_proposal (a_id, created_at);
        CREATE INDEX match_proposal_b_idx ON match_proposal (b_id, created_at);
        CREATE INDEX match_proposal_pending_expiry_idx ON match_proposal (expires_at)
            WHERE state IN ('proposed','a_accepted','b_accepted');

        CREATE TABLE joint_idea (
            id uuid PRIMARY KEY,
            match_id uuid NOT NULL,
            match_state text NOT NULL DEFAULT 'mutual' CHECK (match_state = 'mutual'),
            ord smallint NOT NULL CHECK (ord >= 0 AND ord <= 2),
            title text NOT NULL,
            question text NOT NULL,
            uses_from_a jsonb NOT NULL,
            uses_from_b jsonb NOT NULL,
            first_step text NOT NULL,
            possible_output text NOT NULL,
            prompt_version text,
            model text,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (match_id, ord),
            FOREIGN KEY (match_id, match_state) REFERENCES match_proposal (id, state) ON DELETE CASCADE
        );

        CREATE TABLE match_block (
            account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            blocked_account_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            match_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (account_id, blocked_account_id)
        );

        -- The only path to identity fields: rows exist only for mutual matches.
        CREATE VIEW match_reveal_v AS
        SELECT mp.id AS match_id, v.viewer_id, v.other_id, r.display_name, r.institution, r.orcid, acc.email,
               ARRAY(SELECT x.openalex_author_id FROM researcher_openalex_id x
                     WHERE x.researcher_id = r.id ORDER BY x.position) AS openalex_ids
        FROM match_proposal mp
        CROSS JOIN LATERAL (VALUES (mp.a_id, mp.b_id), (mp.b_id, mp.a_id)) AS v(viewer_id, other_id)
        JOIN account acc ON acc.id = v.other_id AND acc.deleted_at IS NULL
        JOIN researcher r ON r.account_id = v.other_id
        WHERE mp.state = 'mutual';

        CREATE VIEW analytics.v_match_funnel AS
        SELECT date_trunc('week', created_at)::date AS week,
               count(*) AS proposed,
               count(*) FILTER (WHERE a_decision = 'accept' OR b_decision = 'accept') AS accepted_any,
               count(*) FILTER (WHERE state = 'mutual') AS mutual,
               count(*) FILTER (WHERE state = 'declined') AS declined,
               count(*) FILTER (WHERE state = 'expired') AS expired
        FROM match_proposal GROUP BY 1;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW IF EXISTS analytics.v_match_funnel, match_reveal_v;
        DROP TABLE IF EXISTS match_block, joint_idea, match_proposal;
        """
    )
