# api — HTTP entry point

FastAPI application. It is the only driving adapter for HTTP: routers translate requests into application
calls and turn errors into problem+json. The app factory is `scout.api.app:create_app`.

## Modules

| Module | Role |
|---|---|
| `app.py` | `create_app(container=None)`. Builds the app, installs middleware, error handlers and routers. The lifespan starts and closes the container, configures telemetry, instruments FastAPI, SQLAlchemy and httpx, and tunes the GC. Tests pass a prepared `Container`. |
| `middleware.py` | `RequestContextMiddleware`: pure ASGI, no body buffering. Reads `X-Request-Id`, `X-Session-Id` and `X-Page`, sets the request context (contextvars in `scout.shared.context`), echoes `X-Request-Id` on the response and logs one `request` line per call. |
| `errors.py` | `install_error_handlers`: maps every error to `application/problem+json` (see below). |
| `logging.py` | `configure_logging`: structlog, JSON by default. Adds request id, session id, account id and the OpenTelemetry trace id to each line. |
| `health.py` | `GET /health` and `GET /ready`. |
| `events.py` | `POST /api/v1/events` (product analytics). |
| `schemas/events.py` | Request and response models for `/api/v1/events`. |
| `export_openapi.py` | Writes the OpenAPI schema for the frontend client: `cd backend && uv run python -m scout.api.export_openapi ../frontend/openapi.json` (`make gen-client` also regenerates the TypeScript client). |

CORS allows the origins in `CORS_ORIGINS` and exposes `X-Request-Id`.

## Endpoints (M0)

| Method and path | Status | Purpose |
|---|---|---|
| `GET /health` | 200 | Liveness. Body `{"status": "ok"}`. Does not touch the database. |
| `GET /ready` | 200 / 503 | Readiness. Pings the database, body `{"status": "ready"}`. Returns 503 (problem+json, `upstream_unavailable`) when the ping fails. |
| `POST /api/v1/events` | 202 | Buffers a batch of 1 to 100 analytics events. Body limit 64 KiB. Each event is checked against the event catalogue; rejected events are reported by index and reason, the rest are accepted. Returns `{"accepted": n, "rejected": [...]}` without waiting for the database. |

The events body is parsed as JSON whatever the `Content-Type` header says. The endpoint is documented for
`application/json` and `text/plain` because `navigator.sendBeacon` cannot set headers.

Each accepted event is passed to `AnalyticsPort.track()` and the session is passed to `touch_session()`.
Both are buffered sinks (see `scout/adapters/observability/`): they never block the request.

## Errors (problem+json)

Every error response uses `Content-Type: application/problem+json` and has these fields:
`type` (`/problems/<code>`), `title`, `status`, `detail`, `code`, `instance` (request path) and `request_id`.

| Source | Status | `code` |
|---|---|---|
| `ScoutError` subclasses (`scout.shared.errors`) | the class's `http_status` | the class's `code` |
| Request validation (FastAPI/Pydantic) | 422 | `validation_failed`, plus an `errors` list |
| Other HTTP errors (Starlette) | the status code | `http_error` |
| Anything else | 500 | `internal_error` |

For 5xx errors the response `detail` is the generic title. The real message goes to the log only.

## Testing

- `backend/tests/unit/api/test_events.py`: 202 response, per-event rejection, batch size limit, `text/plain` beacons, a slow sink not slowing the response.
- `backend/tests/unit/test_api_basics.py`: `/health`, the `X-Request-Id` header, and problem+json for domain, validation and unhandled errors.
- `backend/tests/integration/test_ready.py`: `/ready` against PostgreSQL (`make test-integration`).
- Load test: `scripts/loadtest_events.py` against a running API (`make loadtest`). Results are in `docs/perf/M0-events-loadtest.md`.

Run: `cd backend && uv run pytest tests/unit/api -q`.
