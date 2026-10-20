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
