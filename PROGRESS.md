# Progress

Merge policy: stack
Current milestone: M1 — Auth + profile claim (backend done, review fixes in progress). M0 done, PR #1 open (M1 added to the same branch)
Current branch: claude/zealous-curie-7m18vd (harness-designated; used instead of m0-foundations)
Last session: 2026-10-20 s01 — M0 tasks 1-12 done, make check green; reviewer + PR pending

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
- [x] 9 Frontend shell, burger menu, tokens [frontend]
- [x] 10 CI workflow
- [x] 10b Documentation scaffolding (journal, ADRs 001–012, PR template, docs-site skeleton)
- [x] 11 LLM port + FakeLLM + llm-router or direct adapter [architect]
- [x] 12 Observability foundations [architect]
- [x] doc-writer pass
- [x] reviewer approve (docs/reviews/M0.md round 2)
- [x] PR opened: https://github.com/fredbuildsai/lattice-scout/pull/1 (stack policy: left open)

## M1 — Auth + profile claim
- [x] B1 `AuthTokenVerifier`, `SupabaseJwksVerifier`, dev auth + `/dev/login` [test-first]
- [x] B2 identity: Account, EnsureAccount, `/me`, `/me/consent`
- [x] B3 `LiteraturePort`, `OpenAlexClient`, `FixtureLiterature`, contract tests
- [x] B4 profile: grouping, claim, import job, toggle [architect: docs/design/M1-identity-profile-claim.md]
- [x] B5 endpoints `/profile/*`
- [x] F1–F3 auth clients, sign-in, claim flow [frontend] (works-confirmation list + retry pending after review)
- [x] doc-writer pass
- [ ] reviewer approve (round 1: changes requested; fixes in progress), PR description update
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
- llmrouter-free is installed out of band: `cd backend && uv pip install -e ../../llmrouter-free` (`uv sync` removes it; router adapter tests skip without it). `uv sync --extra llm` for the OpenAI Agents SDK.
- Run `make check` (needs network for markdownlint via npx and Playwright is separate: `make e2e` with API + `pnpm dev` running).
- Review `docs/reviews/M0.md` round 1 fixed; known gap: API p95 at stress load (DECISIONS 24).

## Open blockers
- B2 live services, B3 llm-router response-text ledger (both have fallbacks; see docs/BLOCKERS.md)
