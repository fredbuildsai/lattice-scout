"""close the PostgREST door for views, functions and every future object

Migration 0013 revoked rights once, over the objects that existed. Supabase grants `anon` and `authenticated`
access to every new object in `public` by default privileges, and views run with their owner's rights (they skip
RLS), so a later view such as `match_reveal_v` would have been readable with the anon key. This migration:

* revokes the default grants for objects created from now on (tables, sequences, functions) in `public` and
  `analytics`, for the migrating role and, when the role exists, for `supabase_admin`;
* revokes what exists today, views and functions included;
* sets `security_invoker = true` on every view (PostgreSQL 15+), so a view is checked with the caller's rights.

The backend connects as the owner, for whom nothing changes. A test asserts that no PostgREST role holds any
privilege on any relation, and that every view is security-invoker.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

UPGRADE = """
DO $$
DECLARE r record; roles text[] := ARRAY[]::text[]; who text;
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN roles := array_append(roles, 'anon'); END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN roles := array_append(roles, 'authenticated'); END IF;
  -- security_invoker on every view, whether or not the PostgREST roles exist
  FOR r IN SELECT c.oid::regclass AS v FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public','analytics') AND c.relkind = 'v' LOOP
    EXECUTE format('ALTER VIEW %s SET (security_invoker = true)', r.v);
  END LOOP;
  -- functions of ours are executable by PUBLIC unless revoked (PostgREST would expose them as RPC)
  FOR r IN SELECT p.oid::regprocedure AS f FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
           WHERE n.nspname IN ('public','analytics')
             AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') LOOP
    EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', r.f);
  END LOOP;
  ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
  IF array_length(roles, 1) IS NULL THEN RETURN; END IF;
  FOREACH who IN ARRAY roles LOOP
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', who);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', who);
    FOR r IN SELECT p.oid::regprocedure AS f FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('public','analytics')
               AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') LOOP
      EXECUTE format('REVOKE ALL ON FUNCTION %s FROM %I', r.f, who);
    END LOOP;
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM %I', who);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA analytics FROM %I', who);
    EXECUTE format('REVOKE USAGE ON SCHEMA analytics FROM %I', who);
    -- objects created from now on, by the migrating role
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM %I', who);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', who);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', who);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA analytics REVOKE ALL ON TABLES FROM %I', who);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA analytics REVOKE ALL ON SEQUENCES FROM %I', who);
    EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA analytics REVOKE ALL ON FUNCTIONS FROM %I', who);
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'supabase_admin') THEN
      BEGIN
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public REVOKE ALL ON TABLES FROM %I', who);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', who);
        EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE supabase_admin IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM %I', who);
      EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'cannot alter default privileges of supabase_admin; run docs/SECURITY.md step as that role';
      END;
    END IF;
  END LOOP;
END $$;
"""

DOWNGRADE = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.oid::regclass AS v FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname IN ('public','analytics') AND c.relkind = 'v' LOOP
    EXECUTE format('ALTER VIEW %s RESET (security_invoker)', r.v);
  END LOOP;
END $$;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
