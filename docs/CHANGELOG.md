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
