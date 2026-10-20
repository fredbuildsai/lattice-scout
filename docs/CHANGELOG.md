# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
- Configuration: `OPENALEX_API_KEY`, `OPENALEX_MAILTO`, `LITERATURE_FIXTURES_DIR`, `DEV_KEYS_DIR` and `DEV_ISSUER` are read by the application from M1 on; they are no longer listed as reserved. `.env.example` lists `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. The configuration reference documents them and the frontend variables.