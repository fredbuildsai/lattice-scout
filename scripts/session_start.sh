#!/usr/bin/env bash
# SessionStart hook for Claude Code cloud sessions.
# Idempotent and tolerant: it must never block a session. Each step is skipped
# if its prerequisite does not exist yet (before M0 created it).
set -u
cd "${CLAUDE_PROJECT_DIR:-$(pwd)}" || exit 0

log() { echo "[session_start] $*"; }

# 1. PostgreSQL 16 (installed but stopped in cloud VMs)
if command -v service >/dev/null 2>&1; then
  service postgresql start >/dev/null 2>&1 && log "postgresql started" || log "postgresql not started"
fi
if [ -f scripts/dev_db.sh ]; then
  timeout 45 bash scripts/dev_db.sh >/dev/null 2>&1 && log "dev databases ready" || log "dev_db.sh failed (see plan M0.7)"
fi

# 2. Backend dependencies
if [ -f backend/pyproject.toml ] && command -v uv >/dev/null 2>&1; then
  (cd backend && timeout 100 uv sync --quiet) && log "uv sync ok" || log "uv sync failed (run it manually)"
fi

# 3. Frontend dependencies (total budget of this script stays under the 300 s hook timeout)
if [ -f frontend/package.json ] && command -v pnpm >/dev/null 2>&1; then
  (cd frontend && timeout 120 pnpm install --prefer-offline --silent) \
    && log "pnpm install ok" || log "pnpm install failed (run it manually)"
fi

# 4. Context for Claude: where we are
if [ -f PROGRESS.md ]; then
  echo "--- PROGRESS.md (head) ---"
  head -n 12 PROGRESS.md
fi
exit 0
