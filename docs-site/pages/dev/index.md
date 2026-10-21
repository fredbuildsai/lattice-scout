# Developer guide

This guide covers how the code is organised, how to run it, and how to test it. The full specification is in
[`docs/ARCHITECTURE.md`](https://github.com/fredbuildsai/lattice-scout/blob/main/docs/ARCHITECTURE.md) and the
build plan is in [`docs/IMPLEMENTATION_PLAN.md`](https://github.com/fredbuildsai/lattice-scout/blob/main/docs/IMPLEMENTATION_PLAN.md).
Milestones M0 (foundations), M1 (authentication and profile claim), M2 (private vault and ingestion) and M3 (gap analysis engine, backend) are in place. Milestones M4 to M7 add the other parts of the product.

Feature pages: [authentication](auth.md) (dev and Supabase modes), [profile and claim](profile.md) (search, grouping, import), [private vault](vault.md) (upload, ingestion, retention, storage), [gap analysis](discovery.md) (runs, fixtures, eval, endpoints), [matching](matching.md) (scoring, filters, reveal, fixtures, seed), [configuration](configuration.md).

## Architecture overview

Lattice Scout is a Python 3.12 backend (FastAPI, Pydantic v2, SQLAlchemy 2 with psycopg 3) and a React and
TypeScript frontend. Data lives in PostgreSQL 16. The backend follows hexagonal architecture
([ADR-001](https://github.com/fredbuildsai/lattice-scout/blob/main/docs/adr/001-hexagonal-architecture-with-per-context.md)):

- **Layers.** `domain` imports nothing outside the standard library and Pydantic. `application` (use cases) imports
  `domain` and `ports`. `adapters` implement the ports. `bootstrap.py` is the composition root that wires them.
  Domain and application code never import FastAPI, SQLAlchemy, httpx, the OpenAI SDK or llm-router.
- **Bounded contexts.** `identity`, `profile`, `vault`, `discovery`, `matching`, `feedback` and `notifications`.
  Contexts call each other through application services or domain events, never through each other's internals.
- **Shared kernel.** `scout/shared` holds ids, the `Clock`, the error tree and the `UnitOfWork` protocol.
  `scout/ports` holds cross-cutting ports: jobs, LLM, usage metering and analytics.
- **Background work.** Requests enqueue jobs in PostgreSQL. A separate worker process claims and runs them
  (see the [worker module guide](https://github.com/fredbuildsai/lattice-scout/blob/main/backend/src/scout/worker/README.md)).
- **Observability never blocks a request.** Usage and analytics writes go to bounded in-process buffers that a
  background thread flushes to the database in bulk (analytics uses `COPY`). When a buffer is full, items are dropped
  and counted, never waited on.
- **Offline by default.** `LLM_BACKEND=fake`, `LITERATURE_BACKEND=fixtures`, `STORAGE_BACKEND=local`,
  `EMAIL_PROVIDER=console` and `AUTH_MODE=dev` need no network. `APP_ENV=prod` refuses these settings.

The status of each module, and the rules behind them, live in its README under `backend/src/scout/<context>/`.

## Repository layout

```text
backend/
  src/scout/
    api/            FastAPI app factory, middleware, problem+json errors, /health, /ready, /api/v1/events
    worker/         python -m scout.worker: job loop and handlers
    bootstrap.py    composition root (Container, build/start/close)
    settings.py     Settings (pydantic-settings), environment variables
    shared/         kernel: ids, clock, errors, events, unit of work, request context
    ports/          cross-cutting ports: jobs, llm, usage, analytics (+ analytics_catalogue)
    adapters/       llm/, observability/, persistence/, queue/
    identity/ profile/ vault/ discovery/ matching/ feedback/ notifications/
                    one folder per bounded context: domain/ application/ ports/ adapters/
  migrations/       Alembic revisions (base tables, observability)
  tests/            unit/, contract/, integration/, live/, fakes/, fixtures/
frontend/           React + TypeScript + Vite + Tailwind (pnpm); analytics client in src/analytics/
config/             llm_router.yaml, llm_prices.yaml, fields.yaml
prompts/            versioned agent prompts (*.md, front-matter per prompt)
scripts/            dev_db.sh, check_docs.py, loadtest_events.py, fetch_fixtures.py, session_start.sh
docs/               ARCHITECTURE, IMPLEMENTATION_PLAN, DECISIONS, adr/, design/, journal/, perf/, CHANGELOG
docs-site/          this site (MkDocs Material)
```

## Running locally

Prerequisites: `uv`, Python 3.12, `pnpm` (frontend only), and PostgreSQL 16 (`scripts/dev_db.sh` starts it on
the VM and creates the `scout` and `scout_test` databases).

```bash
make db          # start PostgreSQL 16 and create the dev and test databases
make migrate     # apply Alembic migrations (cd backend && uv run alembic upgrade head)
make dev         # migrate, then API (port 8000, reload), worker and, if frontend/node_modules exists, the Vite dev server
```

Run the processes separately if you prefer:

```bash
make api         # uvicorn scout.api.app:create_app --factory --reload --port 8000
make worker      # python -m scout.worker
curl -s localhost:8000/health    # {"status":"ok"}
curl -s localhost:8000/ready     # {"status":"ready"} when the database answers
```

Install dependencies once: `cd backend && uv sync` (add `--extra llm` for the OpenAI Agents SDK adapter), and
`cd frontend && pnpm install`.

Settings come from environment variables (or a git-ignored `.env`). Copy `.env.example` to start. Every variable
is listed in the [configuration reference](configuration.md).

## Make targets

| Target | What it does |
|---|---|
| `make db` | Start PostgreSQL and create the dev and test databases (`scripts/dev_db.sh`). |
| `make migrate` | Apply Alembic migrations to the dev database. |
| `make api` | API with reload on port 8000. |
| `make worker` | Job worker. |
| `make dev` | Migrate, then API, worker and (if installed) the frontend dev server. |
| `make lint` | Ruff check and format check on `backend/src` and `backend/tests`. |
| `make typecheck` | mypy on `backend/src` and `backend/tests`. |
| `make contracts` | import-linter contracts (layering and context isolation). |
| `make test` | Unit and contract tests. No database. Skips the `integration`, `needs_network` and `needs_supabase` markers. |
| `make test-integration` | Tests marked `integration`. Needs PostgreSQL (starts it first). |
| `make fe-check` | Frontend lint, typecheck, unit tests and build. |
| `make gen-client` | Export the OpenAPI schema to `frontend/openapi.json` and regenerate the TypeScript client. |
| `make e2e` | Playwright smoke test in `frontend/` (`pnpm e2e`). |
| `make docs-check` | interrogate (docstring coverage), `scripts/check_docs.py` (internal links, ADR format). |
| `make docs` | Build this site in strict mode. |
| `make check` | Everything CI runs: lint, typecheck, contracts, docs-check, test-integration, test, fe-check. |
| `make loadtest` | Event-ingestion load test against a running API (see `docs/perf/`). |

`make eval` is a placeholder until milestone M3.

## Testing

Test in this order: fakes first, then contracts, then integration.

- **Fakes.** Every port has an in-memory double in `backend/tests/fakes/` (job queue, unit of work, usage meter,
  analytics, recorders). Use cases are unit-tested against fakes only.
- **Contract tests.** `backend/tests/contract/` runs one behaviour suite against several implementations (for
  example the job queue on memory and on PostgreSQL). Architecture contracts are in
  `backend/tests/unit/test_architecture_contracts.py`.
- **Integration.** Tests marked `integration` need PostgreSQL 16 (`make test-integration`).
- **Live.** Tests marked `needs_network` call external services (for example the real LLM adapters in
  `backend/tests/live/`). Tests marked `needs_supabase` need Supabase credentials. Both are skipped unless you select
  them explicitly with `-m`.

Run one file while you work: `cd backend && uv run pytest tests/unit/api -q`.

## Where decisions are recorded

| What | Where |
|---|---|
| Decision register (ARCHITECTURE §0.1) | `docs/ARCHITECTURE.md` |
| Small decisions, dated | `docs/DECISIONS.md` |
| Significant decisions (context, options, decision, consequences) | `docs/adr/NNN-title.md`; template in `docs/adr/000-template.md` |
| Design notes for architect-led tasks | `docs/design/M<n>-<task>-<slug>.md` (index: `docs/design/README.md`) |
| Blockers and the human steps they need | `docs/BLOCKERS.md` |
| Session journals | `docs/journal/YYYY-MM-DD-sNN.md` |
| Changes, by version | `docs/CHANGELOG.md` |

## Configuration

Every variable, its default and its purpose: [configuration reference](configuration.md).
