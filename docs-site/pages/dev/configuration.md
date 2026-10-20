# Configuration

Every variable is listed with its safe default in [`.env.example`](https://github.com/fredbuildsai/lattice-scout/blob/main/.env.example). Defaults need no network: fake LLM, fixture literature, local storage, console email, dev auth.

Settings are read by `backend/src/scout/settings.py` (`Settings`, pydantic-settings). Values come from the environment or from `.env` (looked up in `backend/` and then in the repository root). Names are case-insensitive. Copy `.env.example` to `.env` to start; `.env` is git-ignored.

## Core

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `dev` | `test`, `dev` or `prod`. `prod` refuses the offline backends (see below). |
| `DATABASE_URL` | `postgresql+psycopg://scout:scout@localhost:5432/scout` | Application database. |
| `TEST_DATABASE_URL` | unset | Database used by integration tests. `scripts/dev_db.sh` creates `scout_test`. |
| `CORS_ORIGINS` | `["http://localhost:5173","http://127.0.0.1:5173"]` | Allowed browser origins (JSON list). |
| `WORKER_POLL_INTERVAL_MS` | `1000` | Worker delay between polls when no job is due. |
| `JOB_LEASE_SECONDS` | `300` | Lock time for a claimed job before another worker may take it. |

`APP_ENV=prod` fails at start-up if any of these is set: `AUTH_MODE=dev`, `LLM_BACKEND=fake`,
`LITERATURE_BACKEND=fixtures`, `EMAIL_PROVIDER=console`.

## Backends

| Variable | Default | Purpose |
|---|---|---|
| `AUTH_MODE` | `dev` | `dev` (local ES256 keys, `POST /api/v1/dev/login`) or `supabase` (verify Supabase JWTs; needs `SUPABASE_URL`). See [authentication](auth.md). |
| `DEV_KEYS_DIR` | `var/dev-keys` | Where `AUTH_MODE=dev` keeps its ES256 key pair (`private.pem`, `public.jwk`), created on first start. Relative paths resolve from the current directory if they exist there, otherwise from the repository root. Git-ignored. |
| `DEV_ISSUER` | `http://localhost:8000/dev-auth` | `iss` claim of dev tokens. Used for both signing and verification; it is a label, not a served URL. |
| `LLM_BACKEND` | `fake` | `fake` (fixtures), `llm_router` (llm-router package) or `openai_direct` (needs `OPENAI_API_KEY`). |
| `LITERATURE_BACKEND` | `fixtures` | `fixtures` (recorded OpenAlex responses, offline) or `openalex` (live API through `OpenAlexClient`, 7-day response cache). |
| `LITERATURE_FIXTURES_DIR` | `tests/fixtures/openalex` | Recorded responses used by `LITERATURE_BACKEND=fixtures` (`manifest.json` and the response files). Resolved like `DEV_KEYS_DIR`. |
| `OPENALEX_API_KEY` | unset | Optional OpenAlex API key, sent with live requests (`LITERATURE_BACKEND=openalex`). Secret; never commit it. |
| `OPENALEX_MAILTO` | unset | Optional contact address sent to OpenAlex with live requests (`mailto` parameter). |
| `STORAGE_BACKEND` | `local` | `local` (folder in `LOCAL_STORAGE_DIR`) or `supabase` (needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`). |
| `LOCAL_STORAGE_DIR` | `var/storage` | Where `STORAGE_BACKEND=local` keeps files. Git-ignored. |
| `EMAIL_PROVIDER` | `console` | `console` (log only) or `resend` (needs `EMAIL_API_KEY` and `EMAIL_FROM`). |
| `EMAIL_API_KEY` | unset | Secret for the email provider. Never commit it. |
| `EMAIL_FROM` | unset | Sender address for outbound email. |

## Supabase (deployed environments)

| Variable | Default | Purpose |
|---|---|---|
| `SUPABASE_URL` | unset | Project URL. Also used to derive the JWKS URL. |
| `SUPABASE_PUBLISHABLE_KEY` | unset | Publishable (anon) key for the frontend. |
| `SUPABASE_SERVICE_ROLE_KEY` | unset | Server-side secret. Never expose it to the frontend or commit it. |
| `SUPABASE_JWKS_URL` | derived from `SUPABASE_URL` | Explicit JWKS endpoint. The expected issuer still comes from `SUPABASE_URL`, which must therefore be set (known gap: with only this variable, startup fails). |

## Private vault and insights

Read by the API (upload limits, `RETENTION_POLICY`) and by the worker (extraction and insight limits). Restart both after a change. The retention and limit rules are in [private vault](vault.md).

| Variable | Default | Purpose |
|---|---|---|
| `RETENTION_POLICY` | `user_choice` | Deployment default for document retention: `keep` (keep the original and text), `discard` (delete the original after reading and keep only quotes and summaries) or `user_choice` (the upload form decides; unticked means discard). Read when the API starts. Provisional until ADR-009 is confirmed. |
| `VAULT_MAX_BYTES` | `26214400` | Largest accepted upload, in bytes (25 MiB). Larger files get 413. |
| `VAULT_MAX_PAGES` | `300` | Largest accepted page count. More pages give `too_many_pages`. |
| `VAULT_MAX_TEXT_CHARS` | `720000` | Largest extracted text, in characters. More gives `text_too_long`. |
| `VAULT_EXTRACT_TIMEOUT_S` | `60` | CPU seconds for the extraction child process. The parent kills it after this value plus 5 seconds (`extraction_timeout`). |
| `VAULT_EXTRACT_MEMORY_MB` | `1024` | Address-space limit of the extraction child process, in MB. |
| `VAULT_MAX_DOCUMENTS` | `50` | Documents per owner, counting failed and rejected ones until deleted. |
| `INSIGHT_FULL_MAX_CHARS` | `120000` | Documents up to this many characters are analysed in one call. Longer ones are split into slices (map-reduce). |
| `INSIGHT_SLICE_CHARS` | `60000` | Size of each slice for the map calls. |
| `INSIGHT_MAP_CONCURRENCY` | `3` | Map calls that run at the same time. |
| `SUPABASE_STORAGE_BUCKET` | `vault` | Bucket for the documents when `STORAGE_BACKEND=supabase`. Keep it private. |

## LLM

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | unset | Secret for `LLM_BACKEND=openai_direct`. Never commit it. |
| `LLM_ROUTER_CONFIG` | `config/llm_router.yaml` | llm-router deployments and routes. |
| `LLM_ROUTER_SOURCE` | unset | Install source for llm-router, for example `git+https://github.com/fredbuildsai/llmrouter-free@v0.1.0`. Used by the install commands below. |
| `LLM_ROUTER_ALLOW_PAID` | `true` | Whether llm-router may use paid deployments. |
| `LLM_ROUTER_MAX_USD_PER_DAY` | `5.0` | Daily spend limit (USD) passed to the llm-router client. |
| `LLM_MODEL_FAST` / `LLM_MODEL_REASONING` | `fast` / `reasoning` | llm-router route names (or model ids for `openai_direct`). |
| `LLM_PRICE_TABLE` | `config/llm_prices.yaml` | Price per million tokens, used to compute `llm_call` cost. |
| `LLM_FIXTURES_DIR` | `backend/tests/fixtures/llm` | Fixtures used by `LLM_BACKEND=fake`. |
| `PROMPTS_DIR` | `prompts` | Versioned agent prompts. |
| `LLM_MAX_CONCURRENCY` | `8` | Threads used for the synchronous router. |

## Observability

| Variable | Default | Purpose |
|---|---|---|
| `ANALYTICS_ENABLED` | `true` | Product analytics on or off. |
| `ANALYTICS_FLUSH_MS` | `2000` | Bulk-insert interval for the buffered analytics writer. |
| `ANALYTICS_QUEUE_MAX` | `20000` | Analytics buffer capacity. Events are dropped (never blocked) when full. |
| `USAGE_FLUSH_MS` | `2000` | Bulk-insert interval for the LLM and external-call usage meter. |
| `USAGE_QUEUE_MAX` | `10000` | Usage buffer capacity. Records are dropped (never blocked) when full. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP endpoint. Trace export is off when empty; trace ids still appear in logs. |
| `OTEL_SERVICE_NAME` | `scout-api` / `scout-worker` | Service name in traces. |
| `OTEL_TRACES_SAMPLER_ARG` | `0.2` | Sampling ratio for new root traces (parent-based). |

## Frontend (Vite)

These are read by Vite from `frontend/` (`frontend/.env`, or the environment) and are not part of `Settings`. Vite does not read the repository-root `.env`: copy the `VITE_` lines into `frontend/.env`. `frontend/.env.example` lists the ones it documents.

| Variable | Default | Purpose |
|---|---|---|
| `VITE_AUTH_MODE` | `dev` | `dev` (e-mail form calls `POST /api/v1/dev/login`) or `supabase` (Google or e-mail link). Must match `AUTH_MODE`. |
| `VITE_SUPABASE_URL` | unset | Supabase project URL. Required when `VITE_AUTH_MODE=supabase`. |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | unset | Supabase publishable key. Required when `VITE_AUTH_MODE=supabase`. It is public, not a secret. |
| `VITE_API_URL` | `http://localhost:8000` | Base URL of the API. |
| `VITE_ANALYTICS_ENABLED` | `true` | `false` turns the frontend analytics client off. |

## Reserved for later milestones

These fields exist in `Settings` and in `.env.example`, but no code reads them yet. They are documented so that the
names stay stable. Each is defined by the milestone in brackets.

| Variable | Default | Milestone | Purpose |
|---|---|---|---|
| `LAUNCH_FIELD` | `bio-based-materials` | not scheduled (ADR-008) | Launch field for the pilot. |
| `RUN_BUDGET_USD` | `1.50` | M3 | Spend limit per analysis run. |
| `RUN_MAX_OPENALEX_CALLS` | `400` | M3 | OpenAlex call limit per run. |
| `RUN_MAX_CORPUS` | `2500` | M3 | Maximum literature corpus size per run. |
| `ANALYTICS_RETENTION_DAYS` | `395` | not scheduled | Retention for analytics rows. |
| `MATCH_MAX_OPEN_PER_WEEK` | `3` | M5 | Matching: open proposals per researcher per week. |
| `MATCH_EXPIRY_DAYS` | `21` | M5 | Matching: days before a proposal expires. |
| `MATCH_EXCLUDE_COAUTHORS` | `true` | M5 | Matching: exclude past co-authors. |
| `MATCH_NON_MEMBERS` | `false` | M5 | Matching: allow matches with non-members (Phase 2). |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | unset | none | Present in `Settings` but unused: ADR-005 keeps embeddings out of the MVP. Do not set them. |

## Using the real LLM router

```bash
cd backend
uv sync --extra llm                      # OpenAI Agents SDK (openai_direct)
uv pip install -e ../../llmrouter-free   # or: uv pip install "$LLM_ROUTER_SOURCE"
LLM_BACKEND=llm_router OPENAI_API_KEY=... uv run uvicorn scout.api.app:create_app --factory
```

The live LLM tests (`backend/tests/live/`, marker `needs_network`) call the real adapters. They are skipped by default.
