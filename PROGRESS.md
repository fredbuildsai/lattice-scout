# Progress

Merge policy: stack
Current milestone: M0 to M6 built. Reviews: M2 to M5 approved or resolved; M4, M5 and M6 blocking and should-fix findings fixed 2026-10-24 (docs/reviews/*.md, Resolution sections); M6 nits F-49 to F-53 open; final reviewer pass before M7 still to run. Since M6 (2026-11-17): privacy rework (ADR 015/016, DECISIONS 129-130: published data only for the pilot), Ask Scout button removed (F-3), live model eval harness (F-46, not yet run with real keys), local Docker run (docker compose up --build, not yet run end to end), multi-record claim ("Also me"), external suggestions (DECISIONS 132, in network and in field). Next: M7 Pilot launch, not started, waits for the owner's go. Then M8 documentation.
Current branch: claude/zealous-curie-7m18vd (harness-designated; used instead of m0-foundations)
Last session: 2026-11-17 s06 — external suggestions, local Docker run, multi-record claim, documentation update (docs/journal/2026-11-17-s06.md)

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
- [x] reviewer round 2: approved (docs/reviews/M3.md)
- [x] PR update
## M4 — Opportunities UX + feedback
## M5 — Matching
## M6 — Notifications, privacy, hardening
- [x] Slice A: account and vault deletion backend, scan test (docs/design/M6-deletion.md)
- [x] Slice B: upload rate limit, global daily budget guard, `GET /system/status` (UI banner comes with slice C)
- [x] Slice C: privacy tab, deletion dialogs, pause banner (notification preferences move to slice D with the emails)
- [x] Slice D: notifications and emails (console sender by default; Resend only with keys; magic-link template in docs/EMAIL_TEMPLATES.md)
- [x] Slice E: RLS, headers, CORS, retention, funnel/ops views, analytics opt-out, audit and eval workflows (docs/SECURITY.md, docs/OPERATIONS.md)
- [x] reviewer: changes required (docs/reviews/M6.md); blocking and should-fix findings fixed 2026-10-24; nits in BACKLOG F-49 to F-53
- [ ] independent security review (not run)
## M7 — Pilot launch (build only)
- [ ] not started; waits for the owner's go (plan §2 M7: invite flow, admin page, DEPLOY.md, PILOT_RUNBOOK.md; review)
## M8 — Documentation

## Session journals
- [2026-10-20 s01](docs/journal/2026-10-20-s01.md)
- [2026-10-21 s04](docs/journal/2026-10-21-s04.md)
- [2026-10-21 s05](docs/journal/2026-10-21-s05.md)
- [2026-10-24 s02](docs/journal/2026-10-24-s02.md)
- [2026-10-28 s03](docs/journal/2026-10-28-s03.md)
- [2026-10-29 s04](docs/journal/2026-10-29-s04.md)
- [2026-11-17 s06](docs/journal/2026-11-17-s06.md)

## Notes for next session

- **Next milestone: M7 Pilot launch (build only).** Not started; the owner has to give the go. Then M8 documentation (plan §0.4c: `doc-writer` for pages, lead verifies commands, `reviewer` last).
- **Reviewer policy (user, 2026-10-22):** from M5 on the `reviewer` agent is skipped to save cost; the user runs reviews later. Record each milestone as "review pending". Still to run before M7: the final `reviewer` pass (BACKLOG F-49 to F-53 and the M6 security review).
- **Local Docker run (2026-11-17):** `docker compose up --build` after `cp .env.docker.example .env` (OPERATIONS, "Run the whole site locally with Docker"). Real OpenAlex and llm-router backends by default; the API needs one provider key. Not run end to end yet (no Docker daemon in the build VM). The first run on a machine with Docker should report the first error from `docker compose logs <service>`. The comment in `docker/Dockerfile` ("Empty = skip") is outdated: the code falls back to the default source.
- **llmrouter-free:** in the Docker image it is installed at build time from `LLM_ROUTER_SOURCE` (default GitHub repository). Outside Docker: `cd backend && uv pip install -e ../../llmrouter-free` (`uv sync` removes it; router adapter tests skip without it). `uv sync --extra llm` for the OpenAI Agents SDK.
- **Multi-record claim ("Also me", 2026-11-17):** on the onboarding page a researcher can claim several OpenAlex records (max 10, `frontend/src/features/profile/claimSelection.ts`). Covered by unit tests only; no Playwright spec yet.
- **External suggestions (DECISIONS 132):** in-network and in-field scopes are built; "out of network" and "out of field" are BACKLOG F-61, the 2x2 advanced search is F-62 (later). The request shape against live OpenAlex is unit-tested only. The demo recordings give no external suggestion for the demo personas.
- **Live model eval (F-46):** harness built and tested offline, not run with real keys. Run it when B5 is done and read the go / no-go per deployment.
- **Privacy (ADR 015, ADR 016, DECISIONS 129 and 130):** the pilot works on published data only. Vault uploads stay on behind a warning checkbox. Confidential use stays off until B6 (provider data-use terms) is done.
- **Ask Scout button removed (F-3):** the chat returns with F-35.
- Run `make check` (markdownlint needs network through npx; Playwright is separate: `make e2e` with API, worker and `pnpm dev` running).
- Review `docs/reviews/M0.md` round 1 fixed; known gap: API p95 at stress load (DECISIONS 24).
- M2 needs the sample PDFs for tests: `cd backend && uv run python ../scripts/make_sample_docs.py --long`. The `InsightExtractor` fixtures are keyed by a hash of the extracted text: `uv run python ../scripts/make_insight_fixtures.py --check` (regenerate without `--check` after upgrading pypdf or reportlab).
- M2 not verified live: Supabase storage (`needs_supabase`) and real LLM output. Docling is deferred (BACKLOG).

## Open blockers and human steps

- **B4 / F-44 (owner):** create the Supabase project and private bucket, check that `anon` reads nothing through PostgREST, point alerts at `analytics.v_ops_signals`, and paste `docs/EMAIL_TEMPLATES.md` into the magic-link template. Steps: BLOCKERS B4, BACKLOG F-44, docs/SECURITY.md.
- **B5 (owner):** provider keys (`OPENROUTER_API_KEY`, `GROQ_API_KEY` and/or `MISTRAL_API_KEY`) for the live tests and the first live eval run (`eval/run_eval.py --live`). Nothing has run against a real model yet.
- **B6 (owner, not blocking, ADR 016):** confirm each provider's data-use terms, record them in ADR 015, then mark its deployments `authorized: true`. Until then Scout runs on published data only and says so.
- **B7 (owner, needs network):** record the public OpenAlex record of Frederick (François) Chesneau with `scripts/fetch_fixtures.py`, then rerun `scripts/calibrate_matching.py`. His pairs are not evaluated until then.
- **B2 and B3 (both have fallbacks):** live services, and the llm-router response-text ledger (see docs/BLOCKERS.md).
- **Docker run:** first end-to-end run on a machine with Docker (see above).

## M4 — Opportunities UX + feedback
- [x] Backend `feedback` context: `GiveFeedback`, `POST/GET /feedback`, migration 0007, `analytics.v_feedback_summary` (DECISIONS 75-76; F-29 closed)
- [x] Frontend: run page, W5 list with ideas chart, W6 detail, shared `<Claim>`/`<SourceMarker>`, thumbs and reason chips, Home briefing, "Analyse with Scout" in the vault
- [x] Docs: CHANGELOG, journal
- [x] reviewer: done 2026-10-21 (docs/reviews/M4.md); blocking and should-fix findings fixed 2026-10-24 (Resolution section)
- Known gaps (frontend): kind counts computed client-side; no year histogram, matching calls, Lattice Press draft or objection status chips (backend has no data for them).

## M5 — Matching
- [x] architect design note (docs/design/M5-matching.md, DECISIONS 77-86)
- [x] Slice A: domain (proposal state machine, reveal rule, scoring, ids), Hypothesis tests, migration 0008, SQL adapters, reveal integration tests
- [x] Slice B: vocabulary, prompts 1.0.1/1.1.0 and fixtures, profiles, ComputeMatches, DecideOnMatch, RevealMatch, nightly, endpoints, fakes, feedback stars (migrations 0009-0010)
- [x] Slice C: seed_dev.py (`make seed`), frontend W7/M7/M8 + Home teaser, docs (README, user and dev pages, CHANGELOG)
- [x] reviewer: done (docs/reviews/M5.md); findings fixed 2026-10-24 except score calibration (F-39)

### Notes from M5 slice B (historical, done)
- Start from docs/design/M5-matching.md §9 slice B. Existing prompts to bump: prompts/{MethodExtractor,MatchExplainer,JointIdeas}.md (inputs without names/region/band, DECISIONS 77-86).
- Still to build: `config/methods.yaml` + loader, `matching/domain/agent_io.py`, `MatchAgentsPort` + `AgentsMatching`, ACL sources, `ComputeResearcherProfile`, `ComputeMatches`, nightly job, `DecideOnMatch`, `RevealMatch`, `MatchingService`, in-memory fakes (tests/fakes/matching.py), feedback `stars`, `api/matches.py`, migration 0009 (profile tables, candidate views), worker handlers, bootstrap, settings, `make gen-client`.
- Slice C: `seed_dev.py`, W7/M7/M8 frontend, docs. Reviewer skipped by user decision (M4 and M5 review pending).

- M5 known issues for the review: the MatchesPage privacy copy says others see only field, region and career stage, but the card also shows shared topics and method texts (fix copy or card); ComputeMatches passes no titles to the anonymity guard and stores only the explainer's `for_a` text; no pagination UI on /matches; no e2e docs screenshots.

## Review follow-up (2026-10-24)
- Fixed the blocking and should-fix findings of the M4, M5 and M6 reviews (docs/reviews/*.md, Resolution sections; docs/journal/2026-10-24-s02.md). New: ADR 015, DECISIONS 106-119, migrations 0015-0016, Playwright specs `opportunities`, `matches`, `privacy`, `test_matching_flow_pg.py`.
- To run the e2e specs: API and worker on the dev database (`make db migrate seed`, `make api`, `make worker`), then `cd frontend && PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers pnpm e2e`. Privacy spec seeds one expensive `llm_call` row with psql and removes it; the API caches the budget for 30 s.
- Deferred (BACKLOG): F-49 to F-53, nits in F-50 to F-52, final `reviewer` pass before M7.
