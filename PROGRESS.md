# Progress

Merge policy: stack
Current milestone: M3 — Gap analysis engine (backend pipeline built, tests and prompts in progress). M2 done and approved after round 2, awaiting human sign-off. M0 and M1 done and awaiting human sign-off; PR #1 open (M1 and M2 added to the same branch)
Current branch: claude/zealous-curie-7m18vd (harness-designated; used instead of m0-foundations)
Last session: 2026-10-20 s01 — M0, M1 and M2 built; M2 done and approved by the reviewer

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
- [x] F1–F3 auth clients, sign-in, claim flow [frontend] 
- [x] doc-writer pass
- [x] reviewer rounds 1–2 (docs/reviews/M1.md), all findings fixed; PR #1 extended with M1
## M2 — Vault + ingestion
- [x] architect design note (docs/design/M2-vault-ingestion.md, DECISIONS 41-50)
- [x] B1 `FileStoragePort`, `LocalFileStorage`, `SupabaseStorage` (needs_supabase contract), in-memory fake
- [x] B2 `TextExtractionPort`, `PdfDocxExtractor` (child process; pypdf, python-docx, pdfplumber fallback; markitdown rejected, docling deferred)
- [x] B3 vault domain, `UploadDocument`, `IngestDocument`, `ExtractInsights` (map-reduce), `VaultService`, `DeleteDocumentJob`, `SweepVault` [test-first: 152 tests by test-engineer]
- [x] B5 `AgentFactory` + `AgentsInsightExtractor`; `InsightExtractor` prompt 1.1.0 and fixtures [prompt-engineer]
- [x] B6 `/documents` endpoints (+ `UploadGuard`), migration 0004
- [x] B7 `scripts/make_sample_docs.py`
- [x] F1-F2 vault screen W3 and polling [frontend-dev]
- [x] doc-writer pass
- [x] reviewer round 1 (docs/reviews/M2.md): 2 blocking, 4 major fixed; round 2 pending
- [x] reviewer round 2: approved (docs/reviews/M2.md); N2 and N4 fixed, N1, N3, N7 in BACKLOG F-17 to F-20
- [x] PR #1 updated (M0 + M1 + M2)
## M3 — Gap analysis engine
- [x] architect design note (docs/design/M3-gap-analysis-engine.md, DECISIONS 56-70)
- [x] domain (run, opportunity, show rule, verification, grouping, coverage, novelty, scoring) with test-engineer tests
- [x] pipeline: start, runner, sweeper, 8 steps, guard and ledger, SQL layer, migrations 0005 and 0006
- [x] endpoints /runs, /opportunities, /evidence, /admin/claims; client regenerated
- [x] test-engineer call 2 (resume, fencing, budgets, hostile drafts, canary)
- [x] prompt-engineer: five prompts, fixtures, eval cases
- [x] acceptance A1, A2, A3 and canary tests on PostgreSQL
- [x] eval harness (`make eval`)
- [x] doc-writer; reviewer round 1 done (docs/reviews/M3.md), fixes in DECISIONS 74
- [ ] reviewer round 2, PR update
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
- M2 needs the sample PDFs for tests: `cd backend && uv run python ../scripts/make_sample_docs.py --long`. The `InsightExtractor` fixtures are keyed by a hash of the extracted text: `uv run python ../scripts/make_insight_fixtures.py --check` (regenerate without `--check` after upgrading pypdf or reportlab).
- M2 not verified live: Supabase storage (`needs_supabase`) and real LLM output. Docling is deferred (BACKLOG).

## Open blockers
- B2 live services, B3 llm-router response-text ledger (both have fallbacks; see docs/BLOCKERS.md)
