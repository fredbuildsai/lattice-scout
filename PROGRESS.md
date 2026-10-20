# Progress

Merge policy: stack
Current milestone: M0 — Foundations
Current branch: claude/zealous-curie-7m18vd (harness-designated; used instead of m0-foundations)
Last session: 2026-10-20 s01 — M0 tasks 1-8, 10, 10b done; 11, 12 (architect design in progress), 9 (frontend) pending

<!-- Merge policy: "stack" (default) = leave each milestone PR open for the human and branch the next milestone from the previous one.
     "self-merge" = squash-merge your own PR when checks are green. The human may change this line at any time. See plan §0.4. -->

## M0 — Foundations
- [x] 1 Monorepo layout
- [x] 2 Backend tooling + import-linter contracts [test-first]
- [x] 3 Shared kernel [architect]
- [x] 4 Composition root [architect]
- [x] 5 FastAPI app factory, health, errors, request ID, structlog
- [x] 6 Worker + PostgresJobQueue + ping job
- [x] 7 Local dev: dev_db.sh, SessionStart hook (extend scripts/session_start.sh), docker-compose, Makefile, docs stubs
- [x] 8 Alembic + first migration
- [ ] 9 Frontend shell, burger menu, tokens [frontend]
- [x] 10 CI workflow
- [x] 10b Documentation scaffolding (journal, ADRs 001–012, PR template, docs-site skeleton)
- [ ] 11 LLM port + FakeLLM + llm-router or direct adapter [architect]
- [ ] 12 Observability foundations [architect]
- [ ] doc-writer pass, reviewer approve, PR

## M1 — Auth + profile claim
## M2 — Vault + ingestion
## M3 — Gap analysis engine
## M4 — Opportunities UX + feedback
## M5 — Matching
## M6 — Notifications, privacy, hardening
## M7 — Pilot launch (build only)
## M8 — Documentation

## Session journals
- [2026-10-20 s01](docs/journal/2026-10-20-s01.md)

## Notes for next session
- First session: follow the kickoff prompt in docs/claude-code-prompts.md.

## Open blockers
- none yet (see docs/BLOCKERS.md)
