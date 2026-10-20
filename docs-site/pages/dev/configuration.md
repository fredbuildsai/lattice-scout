# Configuration

Every variable is listed with its safe default in [`.env.example`](https://github.com/fredbuildsai/lattice-scout/blob/main/.env.example). Defaults need no network: fake LLM, fixture literature, local storage, console email, dev auth.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `dev` | `test`, `dev` or `prod`. `prod` refuses `AUTH_MODE=dev`. |
| `DATABASE_URL` | `postgresql+psycopg://scout:scout@localhost:5432/scout` | Application database. |
| `LLM_BACKEND` | `fake` | `fake`, `llm_router` or `openai_direct`. |
| `LITERATURE_BACKEND` | `fixtures` | `fixtures` or `openalex`. |
| `STORAGE_BACKEND` | `local` | `local` or `supabase`. |
| `EMAIL_PROVIDER` | `console` | `console` or `resend`. |
| `AUTH_MODE` | `dev` | `dev` (local keys) or `supabase`. |
| `ANALYTICS_ENABLED` | `true` | Product analytics on/off. |
| `ANALYTICS_FLUSH_MS` | `2000` | Bulk-insert interval for the buffered analytics writer. |
| `USAGE_FLUSH_MS` | `2000` | Bulk-insert interval for the LLM/external usage meter. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | OTLP endpoint; tracing export is off when empty. |
| `LLM_FIXTURES_DIR` | `backend/tests/fixtures/llm` | Fixtures used by `LLM_BACKEND=fake`. |
| `PROMPTS_DIR` | `prompts` | Versioned agent prompts. |
| `LLM_MODEL_FAST` / `LLM_MODEL_REASONING` | `fast` / `reasoning` | llm-router route names (or model ids for `openai_direct`). |
| `LLM_ROUTER_CONFIG` | `config/llm_router.yaml` | llm-router deployments and routes. |
| `LLM_MAX_CONCURRENCY` | `8` | Threads used for the synchronous router. |
| `ANALYTICS_QUEUE_MAX` / `USAGE_QUEUE_MAX` | `20000` / `10000` | Buffer capacity; items are dropped (never blocked) when full. |
| `WORKER_POLL_INTERVAL_MS` / `JOB_LEASE_SECONDS` | `1000` / `300` | Worker polling and job lease. |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | Allowed browser origins. |

## Using the real LLM router

```bash
cd backend
uv sync --extra llm                      # OpenAI Agents SDK (openai_direct)
uv pip install -e ../../llmrouter-free   # or: uv pip install "$LLM_ROUTER_SOURCE"
LLM_BACKEND=llm_router OPENAI_API_KEY=... uv run uvicorn scout.api.app:create_app --factory
```
