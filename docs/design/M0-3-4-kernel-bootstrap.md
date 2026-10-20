# M0.3 + M0.4: shared kernel and composition root

**Goal (plan M0.3):** `shared/` kernel: `EntityId`, `Clock` port + `SystemClock` + `FixedClock`, `DomainError` hierarchy, `DomainEvent`, `UnitOfWork` protocol.
**Goal (plan M0.4):** `bootstrap.py` composition root with a `Container` (plain functions, no DI framework) selecting real or fake adapters from settings (`pydantic-settings`).
**Acceptance:** import-linter green (contracts only strengthened); mypy strict on `shared`, `ports`, `settings`, `bootstrap`; `build_container(Settings(app_env="test"))` opens no socket and imports no `openai`/`agents`/`llmrouter_free`; `APP_ENV=prod` + `AUTH_MODE=dev` fails at startup. Feeds M0.5 (problem+json), M0.6 (worker), M0.11/12.

## 1. Where things live (decisions 5, 6)
| Path | Holds | Imports allowed |
|---|---|---|
| `scout/shared/` | kernel: `ids.py`, `clock.py`, `errors.py`, `events.py`, `uow.py` (stdlib-only impls `SystemClock`, `FixedClock`, `UuidGenerator`, `SequentialIdGenerator` included) | stdlib, pydantic |
| `scout/ports/` (**new**) | cross-cutting outbound ports: `jobs.py` now; `llm.py`, `usage.py`, `analytics.py` in M0.11/12 | stdlib, pydantic, `scout.shared` |
| `scout/<ctx>/ports/` | context ports (repos, `LiteraturePort`, ...) | same |
| `scout/settings.py` | `Settings` | pydantic-settings |
| `scout/adapters/<area>/` | real adapters **and runtime-selectable offline adapters**: `llm/fake_llm.py`, `literature/fixtures.py`, `storage/local.py`, `email/console.py`, `auth/dev_verifier.py`, `persistence/db.py`, `queue/postgres.py` | anything |
| `backend/tests/fakes/` | test-only in-memory doubles (`InMemoryUnitOfWork`, `InMemoryJobQueue`, in-memory repos, recorders) + **re-exports** of the runtime offline adapters and `FixedClock`/`SequentialIdGenerator`, so tests import every double from `tests.fakes` | anything |

Rule: `src/` never imports `tests`. Persistence is always Postgres at runtime (D9), so in-memory repos/UoW/queue are test-only.

**pyproject changes (strengthen only):** add `scout.ports` to contract 2 `source_modules`; add `scout.settings`, `pydantic_settings` to `forbidden_modules` of contracts 1 and 2; add `scout.ports.*`, `scout.settings`, `scout.bootstrap` to the mypy strict override.

## 2. Kernel types (`scout/shared`)
**ids.py** (decision 7)
- `EntityId: TypeAlias = UUID`. Per aggregate, in its context domain: `AccountId = NewType("AccountId", UUID)` etc. No wrapper class: pydantic, SQL and JSON handle UUID natively; mypy strict stops mixing ids.
- `class IdGenerator(Protocol): def new(self) -> UUID`.
- `UuidGenerator` (uuid4). `SequentialIdGenerator(start: int = 1)` -> `UUID(int=start)`, `UUID(int=start+1)`, ...
- `def parse_id(raw: str) -> UUID` raising `ValidationFailed` on bad input.

**clock.py**
- `class Clock(Protocol): def now(self) -> datetime` (always tz-aware UTC).
- `SystemClock`: `datetime.now(UTC)`.
- `FixedClock(at: datetime)`: `now()`, `set(at)`, `advance(delta: timedelta)`. Rejects naive datetimes and negative deltas (`ValueError`).

**errors.py** (decision 8). Exceptions, no `Result` type. Each class carries HTTP hints as `ClassVar`s (`code: str`, `http_status: int`, `title: str`); no FastAPI import.
`__init__(self, detail: str, *, extra: Mapping[str, str | int | bool | None] | None = None)`. `extra` holds user-safe fields only.
`to_problem(self) -> dict[str, object]` -> `{"type": f"/problems/{code}", "title", "status", "detail", "code", **extra}`. M0.5 adds `instance` and `request_id`.

| Class (parent) | code | status |
|---|---|---|
| `ScoutError` (Exception) | `internal_error` | 500 |
| `DomainError` (ScoutError) | `domain_error` | 400 |
| `NotFound` (DomainError) | `not_found` | 404 |
| `Conflict` (DomainError) | `conflict` | 409 |
| `InvalidStateTransition` (Conflict) | `invalid_state_transition` | 409 |
| `InvariantViolation` (DomainError) | `invariant_violation` | 422 |
| `ValidationFailed` (DomainError) | `validation_failed` | 422 |
| `Unauthenticated` (DomainError) | `unauthenticated` | 401 |
| `Forbidden` (DomainError) | `forbidden` | 403 |
| `RateLimited` (DomainError) | `rate_limited` | 429 (M6 budget guard) |
| `InfrastructureError` (ScoutError) | `upstream_unavailable` | 503 (adapters wrap DB/httpx failures) |

Privacy rule: if a resource exists but the caller may not know it exists (another user's match, document or run), raise `NotFound`, not `Forbidden`. On 5xx the handler sends a generic detail.

**events.py** (decision 10)
- `class DomainEvent(BaseModel)`, `frozen=True`: `name: ClassVar[str]` (e.g. `"identity.account_created"`), `event_id: UUID`, `occurred_at: AwareDatetime`.
- `@dataclass class AggregateRoot`: `_events: list[DomainEvent] = field(default_factory=list, init=False, repr=False, compare=False)`, `record_event(e) -> None`, `pull_events() -> list[DomainEvent]` (returns the events and clears them). Entities are stdlib dataclasses; value objects are frozen pydantic models (decision 12).
- Dispatch is deferred to the first consumer. Durable cross-context side effects go through jobs.

**uow.py**
```python
class UnitOfWork(Protocol):
    async def __aenter__(self) -> Self: ...
    async def __aexit__(self, et: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
UnitOfWorkFactory: TypeAlias = Callable[[], UnitOfWork]
```
Each context declares `XUnitOfWork(UnitOfWork, Protocol)` with repo attributes (`accounts: AccountRepo`). Services take a factory and open one UoW per use case (no nesting). `__aexit__` rolls back anything not committed.

## 3. JobQueuePort (`scout/ports/jobs.py`, implemented in M0.6; decision 11)
- `JobStatus(StrEnum)`: `queued, running, succeeded, dead`.
- `JobSpec(BaseModel, frozen)`: `kind: str`, `payload: dict[str, JsonValue] = {}`, `run_at: AwareDatetime | None = None`, `max_attempts: int = 5` (ge=1), `dedupe_key: str | None = None`.
- `Job(BaseModel, frozen)`: `id: UUID`, `kind`, `payload`, `status: JobStatus`, `attempts: int`, `max_attempts: int`, `run_at: AwareDatetime`, `locked_by: str | None`, `locked_at: AwareDatetime | None`, `last_error: str | None`.
- Port (as in ARCHITECTURE §5): `enqueue(job: JobSpec) -> UUID`, `claim(worker_id: str, kinds: list[str]) -> Job | None`, `complete(job_id: UUID, result: dict[str, JsonValue]) -> None`, `fail(job_id: UUID, error: str, retry_in: int | None) -> None`.
- `def retry_delay(attempt: int) -> int` (pure function): `min(5 * 2 ** (attempt - 1), 600)` seconds, no jitter.
- `job` table (M0.8): `id uuid pk, kind text, payload jsonb, status text check, attempts int, max_attempts int, run_at timestamptz, locked_by text, locked_at timestamptz, last_error text, result jsonb, dedupe_key text, created_at, updated_at`. Indexes: partial `(run_at) WHERE status='queued'`; unique `(kind, dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running')`.
- Adapters take `Clock` and pass `now` as a parameter. Never use SQL `now()` for queue times.

## 4. Settings (`scout/settings.py`)
`class Settings(BaseSettings)`, `model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore", case_sensitive=False)`. Field = snake_case of the env var. Every non-`VITE_*` key in `.env.example` has a field:
- `app_env: Literal["test","dev","prod"] = "dev"`, `database_url: str` (default as in .env.example), `test_database_url: str | None`.
- `llm_backend: Literal["fake","llm_router","openai_direct"] = "fake"`, `literature_backend: Literal["fixtures","openalex"] = "fixtures"`, `storage_backend: Literal["local","supabase"] = "local"`, `email_provider: Literal["console","resend"] = "console"`, `auth_mode: Literal["dev","supabase"] = "dev"`, `local_storage_dir: Path`.
- Secrets as `SecretStr | None`: `supabase_service_role_key`, `openai_api_key`, `openalex_api_key`, `email_api_key`. Plain `str | None`: `supabase_url`, `supabase_publishable_key`, `supabase_jwks_url`, `openalex_mailto`, `email_from`, `llm_router_source`, `otel_exporter_otlp_endpoint`.
- `llm_router_config: Path`, `llm_model_reasoning: str`, `llm_model_fast: str`, `llm_price_table: Path`, `launch_field: str`, `run_budget_usd: Decimal`, `run_max_openalex_calls: int`, `run_max_corpus: int`, `match_max_open_per_week: int`, `match_expiry_days: int`, `match_exclude_coauthors: bool`, `match_non_members: bool`, `retention_policy: Literal["keep","discard","user_choice"]`, `analytics_enabled: bool`, `analytics_flush_ms: int`, `analytics_retention_days: int`, `usage_flush_ms: int`, `otel_traces_sampler_arg: float`, `embedding_model: str | None`, `embedding_dim: int | None`.
- New (add to `.env.example`): `EMAIL_API_KEY=`, `EMAIL_FROM=`, `WORKER_POLL_INTERVAL_MS=1000`, `JOB_LEASE_SECONDS=300`.
- `model_validator(mode="after")`: if `app_env == "prod"`, reject `auth_mode=dev` (D16), `llm_backend=fake`, `literature_backend=fixtures` and `email_provider=console`. Credential checks: `storage_backend=supabase` requires `supabase_url` and the service key; `auth_mode=supabase` requires a JWKS URL (`jwks_url` property falls back to `${supabase_url}/auth/v1/.well-known/jwks.json`); `openai_direct` requires `openai_api_key`; `resend` requires `email_api_key` and `email_from`.
- `get_settings()` is cached with `lru_cache` and used only by entry points. Domain, application and ports never see `Settings`: bootstrap passes plain values or small frozen config dataclasses defined in `application`.

## 5. Composition root (`scout/bootstrap.py`)
```python
@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings; clock: Clock; ids: IdGenerator; db: Database
    # job_queue (M0.6); usage_meter, analytics, llm (M0.11/12); literature, auth_verifier (M1); storage, extractor (M2); email (M6)
def build_container(settings: Settings, *, clock: Clock | None = None, ids: IdGenerator | None = None) -> Container
async def start_container(c: Container) -> None   # starts buffered writers (M0.12); no-op now
async def close_container(c: Container) -> None   # flush buffers, then engine.dispose()
```
- `Database` (`adapters/persistence/db.py`): `engine: AsyncEngine`, `sessionmaker`, `async ping() -> None`. `create_async_engine` is lazy, so building opens no connection.
- One private selector per backend, e.g. `_select_llm(s, usage) -> LLMPort`: `match s.llm_backend:` with `assert_never` on the fall-through. **Optional/heavy adapters are imported inside their branch** (`openai`, `agents`, `llmrouter_free`), so the default install and CI never import them.
- Use cases are built by plain functions over the container (`def claim_profile(c: Container) -> ClaimProfile`), called per request or job. Tests override with `dataclasses.replace(c, clock=FixedClock(...))`.
- API (M0.5): the lifespan calls `build_container(get_settings())`, then start, store on `app.state.container`, close on shutdown. The worker (M0.6) does the same.

## 6. Invariants the tests assert
1. `SystemClock`/`FixedClock` return aware UTC; `FixedClock` rejects naive datetimes and negative `advance`.
2. `SequentialIdGenerator` is deterministic (`UUID(int=1)`, 2, ...); `UuidGenerator` yields version 4.
3. Every `ScoutError` subclass has a unique `code` and a status in 400..599; subclasses of `Conflict` stay 409; `to_problem()` has exactly the keys above plus `extra`.
4. `DomainEvent` is frozen and rejects naive `occurred_at`; `pull_events()` returns the events in order, then is empty.
5. `InMemoryUnitOfWork`: exiting without commit rolls back; an exception in the block rolls back and propagates; committed state is visible to the next UoW.
6. Settings: prod + `AUTH_MODE=dev` raises `ValidationError` (same for fake LLM, fixtures literature, console email); `Settings(_env_file=None)` gives the offline defaults; every non-VITE key of `.env.example` maps to a field (test parses the file).
7. `build_container(test settings)`: no `openai`/`agents`/`llmrouter_free` in `sys.modules` afterwards, and no DB connection.
8. No file under `backend/src` contains `import tests` or `from tests` (grep test).
9. JobQueue contract (runs on `InMemoryJobQueue` now and on `PostgresJobQueue` in M0.6 as an integration test): a job is claimed at most once, including under concurrent claims; `run_at > now` is not claimable; `claim` filters by `kinds`; `attempts` goes up on claim; `fail(retry_in=n)` requeues it at `now + n`; at `attempts >= max_attempts` or `retry_in=None` it becomes `dead`; a running job whose lease is older than `JOB_LEASE_SECONDS` can be claimed again; the same `(kind, dedupe_key)` while active returns the same id.

## 7. Test plan
Unit tests with fakes: `tests/unit/shared/test_{clock,ids,errors,events}.py`, `tests/unit/test_settings.py`, `tests/unit/test_bootstrap.py`, `tests/unit/test_no_tests_import.py`. Property-based (Hypothesis, if added to the dev group): `FixedClock.advance` sums; `retry_delay` is monotonic and capped. Contract: `tests/contract/test_job_queue.py`, parametrised over implementations.

## 8. Implementation order
1. `backend/pyproject.toml`: contract and mypy changes (section 1); run `lint-imports` and the contract test.
2. `shared/ids.py`, `clock.py`, `errors.py`, `events.py`, `uow.py`, `shared/__init__.py` re-exports, with tests.
3. `scout/ports/__init__.py`, `ports/jobs.py` (types, port, `retry_delay`).
4. `tests/fakes/uow.py`, `tests/fakes/jobs.py`, `tests/fakes/__init__.py` (re-exports); the job contract test.
5. `scout/settings.py` + `.env.example` additions + `test_settings.py`.
6. `adapters/persistence/db.py` (`Database`), `bootstrap.py`, `test_bootstrap.py`, the no-tests-import test.
7. Docs: `backend/src/scout/shared/README.md` (short), journal, tick M0.3/M0.4.

## 9. Risks
- Dataclass inheritance: `AggregateRoot._events` must be `init=False`, or subclasses with non-default fields fail.
- `.env` is resolved relative to the working directory: tests must use `_env_file=None` so a developer's `.env` does not leak in.
- The runtime `FakeLLM` (M0.11) must take its fixture directory from settings, not import `tests`. The Docker image does not ship `tests/`.
- The import-linter contract test rebuilds config from the real pyproject, so new modules (`scout.ports`, `scout.settings`) are covered automatically.
