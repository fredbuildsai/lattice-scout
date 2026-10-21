#!/usr/bin/env bash
# Idempotent: start PostgreSQL 16 (if needed) and create roles/databases `scout` and `scout_test`.
# Works on the cloud VM (service postgresql) and on machines where Postgres already runs.
set -euo pipefail

PGUSER_SCOUT="${SCOUT_DB_USER:-scout}"
PGPASS_SCOUT="${SCOUT_DB_PASSWORD:-scout}"

run_psql() {
  if [ "$(id -u)" = "0" ] && id postgres >/dev/null 2>&1; then
    su postgres -c "psql -v ON_ERROR_STOP=1 -tAq $*"
  else
    psql -v ON_ERROR_STOP=1 -tAq "$@"
  fi
}

if ! pg_isready -q 2>/dev/null; then
  service postgresql start >/dev/null 2>&1 || true
  for _ in $(seq 1 20); do pg_isready -q 2>/dev/null && break; sleep 1; done
fi
pg_isready -q || { echo "PostgreSQL is not running" >&2; exit 1; }

run_psql -c "\"SELECT 1 FROM pg_roles WHERE rolname='${PGUSER_SCOUT}'\"" | grep -q 1 \
  || run_psql -c "\"CREATE ROLE ${PGUSER_SCOUT} LOGIN SUPERUSER PASSWORD '${PGPASS_SCOUT}'\""
for db in scout scout_test; do
  run_psql -c "\"SELECT 1 FROM pg_database WHERE datname='${db}'\"" | grep -q 1 \
    || run_psql -c "\"CREATE DATABASE ${db} OWNER ${PGUSER_SCOUT}\""
done
echo "databases ready: postgresql://${PGUSER_SCOUT}:***@localhost:5432/{scout,scout_test}"
