"""row level security on every table, no policies: PostgREST roles see nothing

The backend connects as the table owner (RLS does not apply to owners). Supabase exposes `public` through
PostgREST to the `anon` and `authenticated` roles; with RLS on and no policy those roles read and write nothing.
A test fails when a table is added without RLS.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

ENABLE = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public','analytics') AND c.relkind IN ('r','p') LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', r.t);
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
    REVOKE USAGE ON SCHEMA analytics FROM anon;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;
    REVOKE USAGE ON SCHEMA analytics FROM authenticated;
  END IF;
END $$;
"""

DISABLE = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS t FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public','analytics') AND c.relkind IN ('r','p') LOOP
    EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', r.t);
  END LOOP;
END $$;
"""


PARTITION_FN = """
CREATE OR REPLACE FUNCTION public.scout_ensure_month_partition(parent regclass, month date) RETURNS void
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
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', part);
    EXECUTE format(
        'WITH m AS (DELETE FROM %I WHERE ts >= %L AND ts < %L RETURNING *) INSERT INTO %I SELECT * FROM m',
        dflt, m_start, m_end, part);
    EXECUTE format('ALTER TABLE %I ATTACH PARTITION %I FOR VALUES FROM (%L) TO (%L)',
                   base, part, m_start, m_end);
END $$;
"""


def upgrade() -> None:
    op.execute(PARTITION_FN)  # new monthly partitions get RLS too
    op.execute(ENABLE)


def downgrade() -> None:
    op.execute(DISABLE)
