"""funnel and operations views; analytics opt-out flag

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notification_pref ADD COLUMN analytics_opt_out boolean NOT NULL DEFAULT false;

        CREATE VIEW analytics.v_funnel_discovery AS
        WITH ev AS (
            SELECT date_trunc('week', created_at)::date AS week, 'signed_up' AS stage, id AS acc FROM account
            UNION ALL SELECT date_trunc('week', created_at)::date, 'claimed', account_id FROM researcher
            UNION ALL SELECT date_trunc('week', created_at)::date, 'run_started', owner_id FROM analysis_run
            UNION ALL SELECT date_trunc('week', created_at)::date, 'run_done', owner_id
                      FROM analysis_run WHERE status = 'done'
            UNION ALL SELECT date_trunc('week', updated_at)::date, 'rated', author_id
                      FROM feedback WHERE target_type = 'opportunity'
        )
        SELECT week,
               count(DISTINCT acc) FILTER (WHERE stage = 'signed_up') AS signed_up,
               count(DISTINCT acc) FILTER (WHERE stage = 'claimed') AS claimed_profile,
               count(DISTINCT acc) FILTER (WHERE stage = 'run_started') AS started_run,
               count(DISTINCT acc) FILTER (WHERE stage = 'run_done') AS finished_run,
               count(DISTINCT acc) FILTER (WHERE stage = 'rated') AS rated_opportunity
        FROM ev GROUP BY week;

        CREATE VIEW analytics.v_funnel_matching AS
        SELECT f.week, f.proposed, f.accepted_any, f.mutual, f.declined, f.expired,
               (SELECT count(*) FROM feedback fb WHERE fb.target_type = 'match'
                  AND date_trunc('week', fb.updated_at)::date = f.week) AS rated
        FROM analytics.v_match_funnel f;

        -- One row: the numbers an alert rule or dashboard tile watches.
        CREATE VIEW analytics.v_ops_signals AS
        SELECT
            (SELECT count(*) FROM job WHERE status = 'dead' AND updated_at > now() - interval '24 hours')
                AS dead_jobs_24h,
            coalesce((SELECT extract(epoch FROM now() - min(run_at)) FROM job
                      WHERE status = 'queued' AND run_at <= now()), 0)::int AS oldest_due_job_age_s,
            (SELECT count(*) FROM analysis_run WHERE status = 'failed' AND updated_at > now() - interval '24 hours')
                AS failed_runs_24h,
            coalesce((SELECT count(*) FILTER (WHERE status <> 'ok')::float / nullif(count(*), 0)
                      FROM llm_call WHERE ts > now() - interval '1 hour'), 0) AS llm_error_rate_1h,
            coalesce((SELECT sum(cost_usd) FROM llm_call WHERE ts >= date_trunc('day', now())), 0) AS llm_cost_today_usd,
            coalesce((SELECT sum(cost_usd) FROM external_call
                      WHERE service = 'openalex' AND ts >= date_trunc('day', now())), 0) AS openalex_cost_today_usd;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP VIEW analytics.v_ops_signals, analytics.v_funnel_matching, analytics.v_funnel_discovery;
        ALTER TABLE notification_pref DROP COLUMN analytics_opt_out;
        """
    )
