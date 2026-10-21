# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- M4 opportunities UX and feedback:
  - `POST /api/v1/feedback` and `GET /api/v1/feedback`: thumbs with five reason chips and a comment; rating again replaces the earlier rating. Migration `20261022_0007_feedback` with the admin view `analytics.v_feedback_summary`.
  - Web screens: run progress page, opportunity list with the novelty and readiness chart, opportunity detail with numbered evidence markers and source popovers, Home briefing, and "Analyse with Scout" in the vault.
- M3 gap analysis engine (backend):
  - `POST /api/v1/runs` (202, 0 to 10 ready documents), `GET /api/v1/runs`, `GET /api/v1/runs/{id}` and `POST /api/v1/runs/{id}/cancel`. A run has eight ordered steps (plan queries, retrieve, group by topic, coverage, measure novelty, analyse, verify, score and persist) and runs as the resumable `discovery.run` job. `discovery.sweep` re-enqueues stalled runs.
  - `GET /api/v1/opportunities`, `GET /api/v1/opportunities/{id}`, `PATCH /api/v1/opportunities/{id}` (shown, saved, dismissed) and `GET /api/v1/evidence/{id}/open`. An opportunity is shown only with at least two distinct verified sources, one of them published literature, and a measured novelty count.
  - Every citation is checked in code against stored text before the Critic reviews a claim. Each claim keeps its status, reason, step, agent, prompt version and model. Unverified claims are stored and never returned.
  - Budgets: exact per-run totals through an atomic ledger, a reservation before each model call, at most 400 OpenAlex calls per run, and per-account limits on runs and spend in 24 hours. A budget hit degrades the step and marks the run `partial`.
  - Resume after a kill: a restarted run continues at its last checkpoint without duplicate rows. A worker that lost its lease stops at its next call or commit.
  - `GET /api/v1/admin/claims` for the admin role. Vault quotes are redacted unless `ADMIN_CLAIMS_SHOW_VAULT_QUOTES=true`, and each view is audited (`admin.claims_viewed`).
  - Migrations `20261021_0005_discovery` (eight tables) and `20261021_0006_discovery_views` (`analytics.v_claim_quality` and `analytics.v_run_cost`).
  - Prompts: QueryPlanner 1.1.0, ClusterLabeler 1.1.0, InsightTopicTagger 1.0.1, GapAnalyst 1.1.0 and Critic 1.1.0. Each prompt states that its input text is data.
  - Field configuration `config/fields.yaml`, the offline eval (`make eval`, `eval/run_eval.py`) and the FakeLLM fixtures for two eval cases, regenerated with `scripts/make_discovery_fixtures.py` (`--check` reports stale fixtures).
- M3 documentation: discovery module README, developer page on gap analysis, user page "Find opportunities", and the configuration reference for the run variables.
- M2 private vault and ingestion (backend):
  - `POST`, `GET`, `PATCH` and `DELETE /api/v1/documents`. Uploads are PDF or DOCX, up to 25 MB; the type is read from the content, and DOCX archives pass zip-bomb limits. The same bytes uploaded again return the existing document (200).
  - Document lifecycle as a state machine (`uploaded`, `extracting`, `extracted`, `analysing`, `ready`, `failed`, `rejected`, `deleting`), with a resumable `vault.ingest` job, `vault.delete` and the `vault.sweep` recovery job (every 10 minutes).
  - Text extraction (`PdfDocxExtractor`): pypdf for PDF with a pdfplumber fallback, python-docx for DOCX. Parsing runs in a child process with memory, CPU and wall-clock limits. Scanned PDFs are refused (`no_text_layer`); there is no OCR.
  - Chunks with page offsets and stable ids. Insights from the `InsightExtractor` agent, map-reduce for long documents, and a check of every item against the text: reviewer quotes must be verbatim. Unverified items are stored and never returned.
  - Retention policies `keep`, `discard` and `user_choice` (D6, ADR-009). With `discard` the original is deleted right after reading, and only verified quotes and summaries are kept.
  - Deletion removes chunks and insights at once. The stored object and the row are removed by `vault.delete`.
  - `FileStoragePort` with `LocalFileStorage` and `SupabaseStorage` adapters (`STORAGE_BACKEND`).
  - `AgentFactory` on the LLM port: prompt loading with placeholders, an `agent.run` span, and error mapping that keeps document text out of exceptions.
  - `InsightExtractor` prompt 1.1.0, `FakeLLM` fixtures for the fictional sample documents, and `scripts/make_sample_docs.py` and `scripts/make_insight_fixtures.py`.
- M2 private vault (frontend): `/vault` page with upload (including the "Keep the original file" choice), status chips, insights with page references, the kind selector and delete with a confirmation.
- M2 documentation: vault module README, developer page on the private vault, user page on the private vault, and the configuration reference for the vault and insight variables.
- M1 review fixes: works confirmation step in onboarding (tick/untick, uncertain works first), Retry import and Search again, neutral `verified_level` (conflicts never shown), per-researcher row locks and advisory locks for claims, non-overwriting works upsert, JWKS throttle with stale-key grace and 503 during outages, Retry-After parsing, 409 on e-mail collisions, 503 when the import cannot be scheduled.
- Hand-off bundle: architecture, implementation plan, screens, runtime prompts, OpenAlex fixtures, Claude Code agents.
- M0 foundations (backend):
  - Python 3.12 project managed with `uv`; import-linter contracts enforce the hexagonal layering and bounded-context isolation.
  - Shared kernel (`scout/shared`): UUID ids, `Clock` (`SystemClock`, `FixedClock`), `ScoutError` tree with problem+json hints, `DomainEvent` and `AggregateRoot`, `UnitOfWork` protocol.
  - Settings with `pydantic-settings`; `APP_ENV=prod` refuses offline backends (`AUTH_MODE=dev`, `LLM_BACKEND=fake`, `LITERATURE_BACKEND=fixtures`, `EMAIL_PROVIDER=console`).
  - Composition root (`scout/bootstrap.py`) that selects real or offline adapters from settings.
  - FastAPI app factory with request-context middleware (`X-Request-Id`, `X-Session-Id`, `X-Page`), structured JSON logs, and RFC 9457 problem+json errors.
  - `GET /health` (liveness) and `GET /ready` (database ping).
  - `POST /api/v1/events`: product-analytics ingestion, returns 202 and writes asynchronously through a buffered writer.
  - Worker (`python -m scout.worker`): polling loop with leases, retries with exponential back-off, and dead-lettering after `max_attempts`.
  - PostgreSQL job queue (`SELECT ... FOR UPDATE SKIP LOCKED`, dedupe keys) with a `ping` job and a monthly partition-maintenance job.
  - LLM port (`LLMPort`, `AgentSpec`, `AgentResult`, `TokenUsage`) with `FakeLLM` (offline, fixture-based), `LlmRouterAdapter` (llm-router package) and `OpenAIAgentsDirectAdapter` (fallback, live tests marked `needs_network`).
  - Usage metering (`llm_call`, `external_call`) and a price table (`config/llm_prices.yaml`).
  - Analytics port and event catalogue, with `app_session` and `analytics_event` tables.
  - OpenTelemetry tracing (OTLP export only when an endpoint is set) and structlog trace ids.
  - Alembic migrations: base tables (`account`, `job`, `audit_log`); observability tables partitioned by month, partition function, and `analytics.*` SQL views.
- M0 foundations (frontend):
  - Application shell with navigation (`frontend/src/Layout.tsx`, `features/nav`).
  - Analytics client (`frontend/src/analytics/`): session id, page-view tracker, batched `track()`, Web Vitals, event catalogue mirror.
- M0 developer tooling:
  - Makefile targets: `db`, `api`, `worker`, `dev`, `migrate`, `lint`, `typecheck`, `contracts`, `test`, `test-integration`, `gen-client`, `fe-check`, `docs-check`, `docs`, `check`.
  - `scripts/dev_db.sh` (PostgreSQL 16 dev and test databases), `docker-compose.yml` and Dockerfile.
  - CI workflow (`.github/workflows/ci.yml`): backend checks, frontend checks, integration tests, and docs checks.
  - `scripts/check_docs.py` (internal links, ADR format) and `scripts/loadtest_events.py`.
  - Pull request template, ADRs 000–013, design notes in `docs/design/`, and the MkDocs Material docs site skeleton (`docs-site/`).
  - Load test for `POST /api/v1/events`: see `docs/perf/M0-events-loadtest.md`.
- M1 authentication and identity (backend):
  - Token verification port (`AuthTokenVerifier`) with two adapters: `SupabaseJwksVerifier` (JWKS, ES256/RS256/EdDSA only, `iss`, `exp`, `role = authenticated`, 10-minute key cache) and dev auth (`AUTH_MODE=dev`, local ES256 key pair in `DEV_KEYS_DIR`, refused when `APP_ENV=prod`).
  - `POST /api/v1/dev/login` (dev mode only).
  - `Account` aggregate with matching and vault-signal consents; vault signals require matching.
  - `IdentityService`: `ensure_account` creates the account on the first authenticated call (race-safe, e-mail synced); `update_consent`.
  - `GET /api/v1/me` and `PATCH /api/v1/me/consent`.
- M1 literature access (backend):
  - `LiteraturePort` with author, work and topic record types.
  - `OpenAlexClient` for `LITERATURE_BACKEND=openalex` (retries on 429 and 5xx, optional `OPENALEX_API_KEY` and `OPENALEX_MAILTO`, 7-day response cache in `openalex_cache`).
  - `FixtureLiterature` for the offline default (`LITERATURE_BACKEND=fixtures`), serving the recorded public OpenAlex records of Johan Foster and Raphaell Moreira.
  - Contract tests that run one behaviour suite against the fixture and the HTTP adapter.
- M1 profile claim (backend):
  - Name compatibility and grouping of split author records. Records that share an ORCID form one candidate; ORCID-less records with a shared institution and a compatible name are shown as unticked possible matches and are never merged automatically.
  - `Researcher` aggregate with verification levels `self_claimed`, `orcid_matched` and `contested`. A researcher and its claimed works are stored per account.
  - `ClaimedWork` with initial inclusion rules (`non_article_type`, `name_mismatch`); user choices survive re-imports; topics computed from included works only.
  - `profile.import` job (resumable per author, dedupe by generation; on the last attempt the researcher is marked `failed`), run by the worker.
  - Endpoints: `GET /api/v1/profile/candidates`, `POST /api/v1/profile/claim` (202, idempotent), `GET /api/v1/profile`, `PATCH /api/v1/profile/works/{work_id}`.
  - Audit rows for `account.created`, `consent.changed`, `profile.claimed` and `profile.claim_conflict`.
  - Migration `0003_identity_profile`: `researcher`, `researcher_openalex_id`, `claimed_work`, `researcher_topic`, `researcher_method` and `openalex_cache`.
- M1 frontend:
  - Sign-in page with the dev client and the Supabase client (`VITE_AUTH_MODE`).
  - Claim flow at `/onboarding`: search, pick a candidate card, choose records, set the matching consent, confirm; the page waits for the import and then goes Home.
  - Home with the import status and the included-works count; "Profile and privacy" page with the two consent switches.
  - Playwright claim test (dev login, claim Johan Foster, import, land on Home).
- M1 documentation: module READMEs for `identity` and `profile`; developer pages on authentication and on the profile claim; user page "Getting started".

### Changed
- Configuration: `RUN_*`, `ACCOUNT_DAILY_BUDGET_USD`, `FIELDS_CONFIG`, `LAUNCH_FIELD` and `ADMIN_CLAIMS_SHOW_VAULT_QUOTES` are read from M3 on and are no longer listed as reserved. The configuration reference documents them.
- Text normalisation (`norm`, `clean_text`) moved from the vault domain to `scout/shared/text.py`. The vault re-exports them, so existing imports keep working.
- Configuration: `RETENTION_POLICY`, `VAULT_*`, `INSIGHT_*` and `SUPABASE_STORAGE_BUCKET` are read from M2 on. `RETENTION_POLICY` is no longer listed as reserved. The default `user_choice` is provisional (ADR-009).
- Configuration: `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `LITERATURE_FIXTURES_DIR`, `DEV_KEYS_DIR` and `DEV_ISSUER` are read by the application from M1 on; they are no longer listed as reserved. `.env.example` lists `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. The configuration reference documents them and the frontend variables.

### Fixed
- Traces and logs no longer carry exception messages. A span processor withholds exception messages and stack traces from finished spans, and a logging filter does the same for the server's own error logs. A database error can no longer copy vault text into a trace (BACKLOG F-17).
- A worker that loses its job lease logs `job_lease_lost` (M2 review N6).

### Notes
- M2 runs offline by default. On 2026-10-20 the vault unit and storage contract tests passed without a database, and `make test-integration` passed (39 tests, including the 30-page acceptance flow with the fake LLM). Not run for this entry: live LLM calls (`needs_network`), live Supabase storage (`needs_supabase`), and the frontend end-to-end test against a running stack.
- Not in M2 (see `docs/BACKLOG.md`): retry for failed documents, switching a kept document to `discard`, OCR and docling, citing vault evidence from opportunities (M3 and M4), and matching on vault content (M5).
- Known issues, tracked as F-11 to F-15 in `docs/BACKLOG.md`: a full vault shows a generic upload error; an empty file is reported as too large; the page wording for Word files without page breaks; a delete-dialog sentence about opportunities that cannot be checked yet; and the zip-bomb message wording.