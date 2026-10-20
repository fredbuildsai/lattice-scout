"""observability: app_session, analytics_event, llm_call, external_call, partitions, analytics views

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PARTITIONED = ("analytics_event", "llm_call", "external_call")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app_session (
            id uuid PRIMARY KEY,
            account_id uuid,
            started_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL,
            user_agent_family text, device_type text, viewport text, app_version text,
            entry_page text, referrer_domain text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX app_session_account_idx ON app_session (account_id, started_at);

        CREATE TABLE analytics_event (
            id uuid NOT NULL,
            ts timestamptz NOT NULL,
            account_id uuid,
            session_id uuid NOT NULL,
            name text NOT NULL,
            page text, prev_page text, dwell_ms integer,
            properties jsonb NOT NULL DEFAULT '{}'::jsonb,
            client_ts timestamptz,
            seq integer,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
        CREATE INDEX analytics_event_session_idx ON analytics_event (session_id, ts);
        CREATE INDEX analytics_event_name_idx ON analytics_event (name, ts);
        CREATE INDEX analytics_event_account_idx ON analytics_event (account_id, ts);

        CREATE TABLE llm_call (
            id uuid NOT NULL,
            ts timestamptz NOT NULL,
            account_id uuid, session_id uuid, run_id uuid,
            feature text NOT NULL, agent text NOT NULL, route text NOT NULL,
            provider text NOT NULL, model text NOT NULL,
            attempt integer NOT NULL DEFAULT 1,
            input_tokens integer NOT NULL, output_tokens integer NOT NULL,
            cached_input_tokens integer, reasoning_tokens integer,
            latency_ms integer NOT NULL,
            cost_usd numeric(12,8) NOT NULL,
            status text NOT NULL, error_type text,
            prompt_version text NOT NULL, trace_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
        CREATE INDEX llm_call_ts_idx ON llm_call (ts);
        CREATE INDEX llm_call_run_idx ON llm_call (run_id);
        CREATE INDEX llm_call_account_idx ON llm_call (account_id, ts);

        CREATE TABLE external_call (
            id uuid NOT NULL,
            ts timestamptz NOT NULL,
            account_id uuid, session_id uuid, run_id uuid,
            service text NOT NULL, endpoint text NOT NULL,
            status text NOT NULL, http_status integer,
            latency_ms integer NOT NULL,
            cost_usd numeric(12,8) NOT NULL DEFAULT 0,
            cache_hit boolean NOT NULL DEFAULT false,
            trace_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts);
        CREATE INDEX external_call_service_idx ON external_call (service, ts);
        CREATE INDEX external_call_run_idx ON external_call (run_id);
        """
    )
    for t in PARTITIONED:
        op.execute(f"CREATE TABLE {t}_default PARTITION OF {t} DEFAULT")

    op.execute(
        """
        CREATE FUNCTION public.scout_ensure_month_partition(parent regclass, month date) RETURNS void
        LANGUAGE plpgsql AS $$
        DECLARE
            m_start timestamptz := date_trunc('month', month)::timestamptz;
            m_end timestamptz := (date_trunc('month', month) + interval '1 month')::timestamptz;
            base text := (SELECT relname FROM pg_class WHERE oid = parent);
            part text := base || '_y' || to_char(month, 'YYYY') || 'm' || to_char(month, 'MM');
            dflt text := base || '_default';
        BEGIN
            IF to_regclass(part) IS NOT NULL THEN
                RETURN;
            END IF;
            EXECUTE format('CREATE TABLE %I (LIKE %I INCLUDING DEFAULTS)', part, base);
            EXECUTE format(
                'WITH m AS (DELETE FROM %I WHERE ts >= %L AND ts < %L RETURNING *) INSERT INTO %I SELECT * FROM m',
                dflt, m_start, m_end, part);
            EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                           base, part, m_start, m_end);
        END $$;
        """
    )
    for t in PARTITIONED:
        for offset in (-1, 0, 1, 2):
            op.execute(
                f"SELECT public.scout_ensure_month_partition('{t}'::regclass, "
                f"(date_trunc('month', now()) + interval '{offset} month')::date)"
            )

    op.execute(
        """
        CREATE SCHEMA analytics;

        CREATE VIEW analytics.v_page_views_daily AS
        SELECT date_trunc('day', e.ts)::date AS day, e.page,
               coalesce(e.account_id, s.account_id) AS account_id,
               count(*) AS views, count(DISTINCT e.session_id) AS sessions
        FROM analytics_event e LEFT JOIN app_session s ON s.id = e.session_id
        WHERE e.name = 'page_view'
        GROUP BY 1, 2, 3;

        CREATE VIEW analytics.v_page_transitions AS
        SELECT e.prev_page AS from_page, e.page AS to_page, count(*) AS transitions,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY e.dwell_ms) AS median_dwell_ms
        FROM analytics_event e
        WHERE e.name = 'page_view' AND e.prev_page IS NOT NULL
        GROUP BY 1, 2;

        CREATE VIEW analytics.v_session_paths AS
        SELECT e.session_id, coalesce(max(e.account_id::text), max(s.account_id::text))::uuid AS account_id,
               min(e.ts) AS started_at,
               array_agg(e.page ORDER BY e.seq, e.client_ts, e.ts) AS pages
        FROM analytics_event e LEFT JOIN app_session s ON s.id = e.session_id
        WHERE e.name = 'page_view'
        GROUP BY e.session_id;

        CREATE VIEW analytics.v_feature_usage AS
        SELECT date_trunc('day', e.ts)::date AS day, e.name, count(*) AS events,
               count(DISTINCT coalesce(e.account_id, s.account_id)) AS users,
               count(DISTINCT e.session_id) AS sessions
        FROM analytics_event e LEFT JOIN app_session s ON s.id = e.session_id
        WHERE e.name NOT IN ('page_view', 'web_vital')
        GROUP BY 1, 2;

        CREATE VIEW analytics.v_llm_usage_daily AS
        SELECT date_trunc('day', ts)::date AS day, account_id, feature, model, count(*) AS calls,
               sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
               sum(coalesce(cached_input_tokens, 0)) AS cached_input_tokens,
               sum(coalesce(reasoning_tokens, 0)) AS reasoning_tokens,
               sum(cost_usd) AS cost_usd, count(*) FILTER (WHERE status <> 'ok') AS errors
        FROM llm_call
        GROUP BY 1, 2, 3, 4;
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS analytics CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.scout_ensure_month_partition(regclass, date)")
    for t in (*PARTITIONED, "app_session"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
