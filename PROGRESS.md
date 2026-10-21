# Progress

Merge policy: stack
Current milestone: M5 — Matching (slice A done; slice B next). M4 built (Opportunities UX + feedback), review pending (skipped for cost). M3 done and approved. M2 done and approved after round 2, awaiting human sign-off. M0 and M1 done and awaiting human sign-off; PR #1 open (M1 and M2 added to the same branch)
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

- **Reviewer policy (user, 2026-10-22):** from M5 on, skip the `reviewer` agent to save cost; the user runs it later. Record each milestone as "review pending" here and in the journal. Pro plan: work in slices that end at a pushed commit.
- llmrouter-free is installed out of band: `cd backend && uv pip install -e ../../llmrouter-free` (`uv sync` removes it; router adapter tests skip without it). `uv sync --extra llm` for the OpenAI Agents SDK.
- Run `make check` (needs network for markdownlint via npx and Playwright is separate: `make e2e` with API + `pnpm dev` running).
- Review `docs/reviews/M0.md` round 1 fixed; known gap: API p95 at stress load (DECISIONS 24).
- M2 needs the sample PDFs for tests: `cd backend && uv run python ../scripts/make_sample_docs.py --long`. The `InsightExtractor` fixtures are keyed by a hash of the extracted text: `uv run python ../scripts/make_insight_fixtures.py --check` (regenerate without `--check` after upgrading pypdf or reportlab).
- M2 not verified live: Supabase storage (`needs_supabase`) and real LLM output. Docling is deferred (BACKLOG).

## Open blockers
- B2 live services, B3 llm-router response-text ledger (both have fallbacks; see docs/BLOCKERS.md)

## M4 — Opportunities UX + feedback
- [x] Backend `feedback` context: `GiveFeedback`, `POST/GET /feedback`, migration 0007, `analytics.v_feedback_summary` (DECISIONS 75-76; F-29 closed)
- [x] Frontend: run page, W5 list with ideas chart, W6 detail, shared `<Claim>`/`<SourceMarker>`, thumbs and reason chips, Home briefing, "Analyse with Scout" in the vault
- [x] Docs: CHANGELOG, journal
- [ ] reviewer: pending (skipped by user decision, to be run later)
- Known gaps (frontend): kind counts computed client-side; no year histogram, matching calls, Lattice Press draft or objection status chips (backend has no data for them).

## M5 — Matching
- [x] architect design note (docs/design/M5-matching.md, DECISIONS 77-86)
- [x] Slice A: domain (proposal state machine, reveal rule, scoring, ids), Hypothesis tests, migration 0008, SQL adapters, reveal integration tests
- [ ] Slice B: methods vocabulary, prompts and fixtures, profiles, ComputeMatches, DecideOnMatch, endpoints, fakes for the ports
- [ ] Slice C: seed_dev.py, frontend W7/M7/M8, docs
- [ ] reviewer: skipped by user decision (review pending)

### Notes for next session (M5 slice B, not started; budget ran out)
- Start from docs/design/M5-matching.md §9 slice B. Existing prompts to bump: prompts/{MethodExtractor,MatchExplainer,JointIdeas}.md (inputs without names/region/band, DECISIONS 77-86).
- Still to build: `config/methods.yaml` + loader, `matching/domain/agent_io.py`, `MatchAgentsPort` + `AgentsMatching`, ACL sources, `ComputeResearcherProfile`, `ComputeMatches`, nightly job, `DecideOnMatch`, `RevealMatch`, `MatchingService`, in-memory fakes (tests/fakes/matching.py), feedback `stars`, `api/matches.py`, migration 0009 (profile tables, candidate views), worker handlers, bootstrap, settings, `make gen-client`.
- Slice C: `seed_dev.py`, W7/M7/M8 frontend, docs. Reviewer skipped by user decision (M4 and M5 review pending).
