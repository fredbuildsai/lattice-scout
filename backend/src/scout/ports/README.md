# ports — cross-cutting outbound ports

Interfaces (`typing.Protocol`, stdlib + pydantic + `scout.shared` only). Per-context ports live in `<context>/ports/`.

| Module | Port / types | Production adapter | Test double |
|---|---|---|---|
| `jobs.py` | `JobQueuePort`, `JobSpec`, `Job`, `retry_delay` | `adapters/queue/postgres.py` (`SKIP LOCKED`, lease, dedupe) | `tests/fakes/jobs.py` |
| `llm.py` | `LLMPort`, `AgentSpec`, `CallContext`, `AgentResult`, `TokenUsage` | `LlmRouterAdapter`, `OpenAIAgentsDirectAdapter` | `FakeLLM` (also the runtime `LLM_BACKEND=fake`) |
| `usage.py` | `UsageMeterPort`, `LlmCallRecord`, `ExternalCallRecord`, `PriceTable` | `BufferedUsageMeter` | `InMemoryUsageMeter` |
| `analytics.py`, `analytics_catalogue.py` | `AnalyticsPort`, `AnalyticsEvent`, `EVENT_CATALOGUE` | `BufferedAnalytics` / `NullAnalytics` | `InMemoryAnalytics` |
| `literature.py` | `LiteraturePort` (`search_authors`, `authors_by_orcid`, `get_author`, `list_author_works`, `search_works`, `get_work`, `count_works`), record types `AuthorRecord`, `AuthorCandidate`, `WorkRecord`, `WorkQuery`, `TopicRef` | `OpenAlexClient` (`LITERATURE_BACKEND=openalex`, cached in `openalex_cache`) | `FixtureLiterature` (offline runtime adapter, also used as the test double; no separate in-memory fake yet) |
| `audit.py` | `AuditLog` (`add(AuditEntry)`), `AuditEntry` (action, actor, target, `meta`) | `SqlAuditLog` (`audit_log` table, written inside the caller's unit of work) | `InMemoryAuditLog` (`tests/fakes/audit.py`) |

Rules: sinks (`record_*`, `track`) are synchronous, never raise and never do I/O on the caller's path.
Cached tokens are part of input tokens and reasoning tokens part of output tokens (OpenAI semantics), so the price
table never charges reasoning on top. The frontend mirrors the catalogue in `frontend/src/analytics/events.ts`;
a test fails on drift. Design notes: `docs/design/M0-3-4-kernel-bootstrap.md`, `docs/design/M0-11-12-llm-observability.md`.
