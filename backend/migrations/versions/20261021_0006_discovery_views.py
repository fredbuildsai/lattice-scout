"""analytics views over discovery: claim quality per prompt version and cost per run

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE VIEW analytics.v_claim_quality AS
        WITH q AS (
            SELECT date_trunc('day', created_at)::date AS day, agent, prompt_version, model, set_by, status,
                   status_reason, count(*) AS n
            FROM claim GROUP BY 1, 2, 3, 4, 5, 6, 7
        )
        SELECT day, agent, prompt_version, model, set_by, status, status_reason, n,
               n::numeric / sum(n) OVER (PARTITION BY day, prompt_version) AS share
        FROM q;

        CREATE VIEW analytics.v_run_cost AS
        SELECT r.id AS run_id, r.owner_id, r.cost_usd, r.llm_calls, r.openalex_calls,
               count(o.id) FILTER (WHERE o.status IN ('shown','saved','dismissed')) AS shown,
               r.cost_usd / NULLIF(count(o.id) FILTER (WHERE o.status IN ('shown','saved','dismissed')), 0)
                   AS cost_per_shown
        FROM analysis_run r LEFT JOIN opportunity o ON o.run_id = r.id
        GROUP BY r.id;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS analytics.v_run_cost, analytics.v_claim_quality")
