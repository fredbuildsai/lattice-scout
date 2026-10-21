"""discovery: analysis_run, run_document, corpus_work, cluster, novelty_probe, opportunity, claim, evidence

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE analysis_run (
            id uuid PRIMARY KEY,
            owner_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            session_id uuid,
            status text NOT NULL CHECK (status IN ('queued','running','done','failed','cancelled')),
            step text CHECK (step IN ('plan_queries','retrieve','group_by_topic','coverage','measure_novelty',
                'analyse','verify','score_persist')),
            step_state jsonb NOT NULL DEFAULT '{}'::jsonb,
            inputs jsonb NOT NULL DEFAULT '{}'::jsonb,
            budget jsonb NOT NULL,
            cost_usd numeric(12,8) NOT NULL DEFAULT 0,
            llm_calls integer NOT NULL DEFAULT 0,
            openalex_calls integer NOT NULL DEFAULT 0 CHECK (openalex_calls >= 0),
            tokens_in bigint NOT NULL DEFAULT 0,
            tokens_out bigint NOT NULL DEFAULT 0,
            generation integer NOT NULL DEFAULT 0,
            executions integer NOT NULL DEFAULT 0,
            partial boolean NOT NULL DEFAULT false,
            degraded jsonb NOT NULL DEFAULT '[]'::jsonb,
            error_code text,
            error_step text,
            cancel_requested_at timestamptz,
            prompt_versions jsonb NOT NULL DEFAULT '{}'::jsonb,
            heartbeat_at timestamptz,
            started_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX analysis_run_one_active_idx ON analysis_run (owner_id)
            WHERE status IN ('queued','running');
        CREATE INDEX analysis_run_owner_created_idx ON analysis_run (owner_id, created_at DESC);
        CREATE INDEX analysis_run_status_heartbeat_idx ON analysis_run (status, heartbeat_at);

        CREATE TABLE run_document (
            run_id uuid NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            document_id uuid NOT NULL REFERENCES document(id) ON DELETE CASCADE,
            label text NOT NULL,
            PRIMARY KEY (run_id, document_id),
            UNIQUE (run_id, label)
        );

        CREATE TABLE cluster (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            key text NOT NULL,
            label_key text NOT NULL,
            label text NOT NULL,
            description text NOT NULL DEFAULT '',
            search_terms jsonb NOT NULL DEFAULT '[]'::jsonb,
            label_source text NOT NULL CHECK (label_source IN ('topic','llm')),
            topic_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
            subfield text,
            size integer NOT NULL,
            user_works integer NOT NULL DEFAULT 0,
            insight_weight real NOT NULL DEFAULT 0,
            user_coverage real NOT NULL DEFAULT 0,
            density real NOT NULL DEFAULT 0,
            candidate_rank integer,
            is_misc boolean NOT NULL DEFAULT false,
            UNIQUE (run_id, key)
        );

        CREATE TABLE corpus_work (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            openalex_work_id text NOT NULL,
            doi text,
            title text NOT NULL,
            abstract text,
            year integer,
            cited_by integer NOT NULL DEFAULT 0,
            type text,
            topics jsonb NOT NULL DEFAULT '[]'::jsonb,
            primary_topic_id text,
            authors jsonb NOT NULL DEFAULT '[]'::jsonb,
            origin text NOT NULL CHECK (origin IN ('search','profile','resolved')),
            query_labels text[] NOT NULL DEFAULT '{}',
            cluster_id uuid REFERENCES cluster(id) ON DELETE SET NULL,
            UNIQUE (run_id, openalex_work_id)
        );
        CREATE INDEX corpus_work_cluster_idx ON corpus_work (run_id, cluster_id);

        CREATE TABLE novelty_probe (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            label text NOT NULL,
            cluster_id uuid REFERENCES cluster(id) ON DELETE SET NULL,
            query text NOT NULL,
            from_date date NOT NULL,
            count integer,
            status text NOT NULL CHECK (status IN ('pending','measured','skipped')),
            measured_at timestamptz,
            UNIQUE (run_id, label)
        );

        CREATE TABLE opportunity (
            id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES analysis_run(id) ON DELETE CASCADE,
            owner_id uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
            batch integer NOT NULL,
            ord integer NOT NULL,
            kind text NOT NULL CHECK (kind IN ('white_spot','synergy','resubmission')),
            title text NOT NULL,
            one_line text NOT NULL DEFAULT '',
            status text NOT NULL CHECK (status IN ('draft','shown','saved','dismissed','rejected_by_critic',
                'unverified','duplicate')),
            status_reason text,
            status_step text,
            duplicate_of uuid,
            cluster_ids uuid[] NOT NULL DEFAULT '{}',
            fit numeric(4,3) CHECK (fit BETWEEN 0 AND 1),
            novelty numeric(4,3) CHECK (novelty BETWEEN 0 AND 1),
            readiness numeric(4,3) CHECK (readiness BETWEEN 0 AND 1),
            effort numeric(4,3) CHECK (effort BETWEEN 0 AND 1),
            rank_score numeric(4,3) CHECK (rank_score BETWEEN 0 AND 1),
            score_basis jsonb NOT NULL DEFAULT '{}'::jsonb,
            novelty_probe_id uuid REFERENCES novelty_probe(id) ON DELETE SET NULL,
            readiness_checklist jsonb NOT NULL DEFAULT '[]'::jsonb,
            effort_estimate text,
            next_steps jsonb NOT NULL DEFAULT '[]'::jsonb,
            analyst_version text,
            critic_version text,
            model text,
            critic jsonb,
            verify_state text NOT NULL DEFAULT 'pending' CHECK (verify_state IN ('pending','done')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, batch, ord)
        );
        CREATE INDEX opportunity_owner_status_idx ON opportunity (owner_id, status, rank_score DESC);

        CREATE TABLE claim (
            id uuid PRIMARY KEY,
            opportunity_id uuid NOT NULL REFERENCES opportunity(id) ON DELETE CASCADE,
            ord integer NOT NULL,
            section text NOT NULL CHECK (section IN ('rationale','objection','score')),
            text text NOT NULL CHECK (char_length(text) <= 300),
            status text NOT NULL CHECK (status IN ('pending','verified','unverified','contradicted',
                'rejected_by_critic')),
            status_reason text,
            set_by text NOT NULL CHECK (set_by IN ('analyse','verify_code','verify_critic','score_persist')),
            agent text,
            prompt_version text,
            model text,
            critic_verdict text,
            critic_reason text,
            critic_fix text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (opportunity_id, ord)
        );
        CREATE INDEX claim_status_reason_idx ON claim (status, status_reason);
        CREATE INDEX claim_prompt_version_idx ON claim (prompt_version);
        CREATE INDEX claim_created_idx ON claim (created_at);

        CREATE TABLE evidence (
            id uuid PRIMARY KEY,
            claim_id uuid NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            ord integer NOT NULL,
            source_type text NOT NULL CHECK (source_type IN ('literature','profile','vault','count')),
            label text NOT NULL,
            ref text NOT NULL,
            quote text CHECK (char_length(quote) <= 300),
            verified boolean,
            check_code text,
            check_detail text,
            corpus_work_id uuid REFERENCES corpus_work(id) ON DELETE CASCADE,
            chunk_id uuid REFERENCES document_chunk(id) ON DELETE CASCADE,
            document_id uuid,
            probe_id uuid REFERENCES novelty_probe(id) ON DELETE CASCADE,
            insight_ref text,
            UNIQUE (claim_id, ord),
            CHECK (verified IS NOT TRUE OR (
                (source_type IN ('literature','profile') AND corpus_work_id IS NOT NULL)
                OR (source_type = 'vault' AND chunk_id IS NOT NULL)
                OR (source_type = 'count' AND probe_id IS NOT NULL)))
        );
        CREATE INDEX evidence_chunk_idx ON evidence (chunk_id) WHERE chunk_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TABLE IF EXISTS evidence, claim, opportunity, novelty_probe, corpus_work, cluster, run_document, "
        "analysis_run CASCADE"
    )
