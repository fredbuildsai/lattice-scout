# adapters — cross-cutting driven adapters

- `persistence/db.py` — `Database`: async engine (requests) + small blocking engine (background writers in threads).
- `queue/postgres.py` — `PostgresJobQueue`.
- `llm/` — `FakeLLM` (offline, fixtures from `backend/tests/fixtures/llm/<Agent>/<hash>.json` or `default.json`),
  `LlmRouterAdapter` (+ `MeteringCompletion`), `OpenAIAgentsDirectAdapter`, `prompts.py` (loads `prompts/*.md`),
  `metering.py`. Heavy imports (`litellm`, `openai`, `agents`, `llmrouter_free`) happen only inside the selected branch of `bootstrap._select_llm`.
- `observability/` — `BufferedWriter` (bounded, drops instead of blocking, drains on close), `BufferedUsageMeter`,
  `BufferedAnalytics` (COPY in a thread), partition job, price table, telemetry (OTLP only if an endpoint is set).

Adapters never import application services (enforced by import-linter). Live tests are marked `needs_network`.
