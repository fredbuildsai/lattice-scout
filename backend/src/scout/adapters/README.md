# adapters — cross-cutting driven adapters

- `persistence/db.py` — `Database`: async engine (requests) + small blocking engine (background writers in threads).
- `persistence/models.py` — SQLAlchemy Core table definitions for `account`, `audit_log`, `researcher`, `researcher_openalex_id`, `researcher_topic` and `claimed_work` (the schema itself is owned by Alembic). The context repositories live in each context's `adapters/sql.py`, not here.
- `persistence/uow.py` — `SqlUnitOfWork`: one `AsyncSession` per use case, rolled back unless committed.
- `persistence/audit.py` — `SqlAuditLog`: writes `audit_log` rows on the caller's session, so they commit with the change.
- `literature/` — the `LiteraturePort` adapters:
  - `openalex.py` — `OpenAlexClient` (httpx, optional `OPENALEX_API_KEY` and `OPENALEX_MAILTO`, retries on 429 and 5xx, usage metering).
  - `cache.py` — `PostgresOpenAlexCache` on the `openalex_cache` table, 7-day TTL.
  - `fixtures.py` — `FixtureLiterature`, the offline default (`LITERATURE_BACKEND=fixtures`), serving the recorded responses in `tests/fixtures/openalex/` (`manifest.json`).
  - `parse.py` — maps OpenAlex JSON to port records; live responses and fixtures share this parser.
- `queue/postgres.py` — `PostgresJobQueue`.
- `llm/` — `FakeLLM` (offline, fixtures from `backend/tests/fixtures/llm/<Agent>/<hash>.json` or `default.json`),
  `LlmRouterAdapter` (+ `MeteringCompletion`), `OpenAIAgentsDirectAdapter`, `prompts.py` (loads `prompts/*.md`),
  `metering.py`. Heavy imports (`litellm`, `openai`, `agents`, `llmrouter_free`) happen only inside the selected branch of `bootstrap._select_llm`.
- `observability/` — `BufferedWriter` (bounded, drops instead of blocking, drains on close), `BufferedUsageMeter`,
  `BufferedAnalytics` (COPY in a thread), partition job, price table, telemetry (OTLP only if an endpoint is set).

Adapters never import application services (enforced by import-linter). Live tests are marked `needs_network`.
