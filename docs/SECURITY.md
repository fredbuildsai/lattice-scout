# Security review (M6)

Reviewed by the lead developer on 2026-10-23, backend and configuration only. It is not an independent audit and not a penetration test. The independent `reviewer` pass with a security focus is still pending (BACKLOG F-34).

## Findings and what was done

| Area | Finding | Action |
|---|---|---|
| Row level security | No table had RLS. On Supabase, PostgREST exposes `public` to the `anon` and `authenticated` roles. | Migration 0013 enables RLS on every table of `public` and `analytics` (including partitions), with no policy, and revokes table rights from both roles. The backend connects as the table owner, which RLS does not restrict. New monthly partitions get RLS from `scout_ensure_month_partition`. `test_every_table_has_row_level_security_and_no_policy` fails when a table is added without RLS. |
| Views, functions and future objects (review M6 #7) | The REVOKE of 0013 ran once; Supabase grants new objects to `anon` and `authenticated` by default, and views skip RLS. | Migration 0015: default privileges revoked, EXECUTE revoked from PUBLIC on our functions, `security_invoker = true` on every view. A test creates the roles with Supabase-like default grants and asserts no privilege on any relation or function. Still to confirm on a real project (BACKLOG F-53). |
| Vault text to LLM providers (review M6 #1) | Free-tier providers with unrecorded data-use terms received every prompt. | ADR 015 and 016: confidential/published classification per input field, confidential parts withheld on routes that are not authorized (the run continues on published data and says so), confidential requests pinned to authorized deployments, per-request model check, minimal environment for the parser child. Human step to enable confidential use: BLOCKERS B6. |
| Storage bucket | Keys are `vault/{account}/{document}` and the bucket is read only through the service-role key. | Create the bucket as private and add no policy on `storage.objects` (SQL below). Human step, see BLOCKERS B4. |
| Response headers | None set. | `SecurityHeadersMiddleware`: `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Content-Security-Policy`, `Cache-Control: no-store`, `Permissions-Policy`, and HSTS in production. |
| CORS | Wildcard methods and headers. | Methods `GET POST PATCH DELETE OPTIONS`; headers `Authorization`, `Content-Type`, `X-Session-Id`, `X-Page`, `X-Request-Id`. Production refuses `*` and `http://` origins. |
| API docs | `/docs`, `/redoc` and `/openapi.json` were served everywhere. | Disabled when `APP_ENV=prod`. |
| Authentication coverage | Not checked mechanically. | `test_every_route_requires_an_account_unless_listed_public` walks all routes. Public on purpose: `/health`, `/ready`, `POST /events` (capped at 64 KB, validated against the event catalogue) and the dev login (only mounted with `AUTH_MODE=dev`, refused in production). |
| Authorization | Admin routes check `role == admin` in the handler; every other route scopes by the account id in the query. | Covered by the isolation tests of each context (M1 to M5). |
| Dependencies | `pip-audit` on the locked backend dependencies and `pnpm audit --prod`: no known vulnerabilities on 2026-10-23. | `make audit` and the weekly workflow `audit.yml`. |
| Secrets | `.env` is git-ignored; `.env.example` holds defaults only. Production refuses the dev defaults for auth, LLM, literature, e-mail, storage and the audit hash key. | Secret scanning is a hard stop in CLAUDE.md; nothing was committed. |
| Traces | Agent traces must not carry prompts or documents. | `trace_include_sensitive_data=False`, exception messages are dropped from spans and logs, and a static test forbids the opposite setting. The llm-router ledger still keeps response, reasoning and error text of every call, confidential ones included, in process memory for the life of the process (B3, F-58); router log records carry route, deployment, outcome and exception class only (ADR 016, amendment 1). |

## Not done

- **Per-IP rate limiting** of the unauthenticated endpoints (`/events`, sign-in). Put it in front of the API (CDN or reverse proxy) or add middleware; BACKLOG F-47.
- **File scanning** of uploads beyond type, size, page and archive limits (M2).
- **Penetration test** and the independent security review.

## Supabase settings to apply (human)

```sql
-- Private bucket for the vault; no policies on storage.objects, so only the service-role key can read or write.
insert into storage.buckets (id, name, public) values ('vault', 'vault', false)
  on conflict (id) do update set public = false;
```

In the Supabase dashboard: disable sign-ups that are not invited when the pilot starts (M7), set the site URL and the allowed redirect URLs to the app origin, and keep the service-role key out of the frontend (`VITE_*` variables must never carry it).
