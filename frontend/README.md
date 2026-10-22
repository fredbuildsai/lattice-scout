# Lattice Scout frontend

React 18 + TypeScript (strict) + Vite + Tailwind + React Router + TanStack Query. Design tokens come from `docs/screens/README.md` and live in `tailwind.config.ts`; fonts are self-hosted through `@fontsource/*`.

## Commands (pnpm)

| Command | What it does |
|---|---|
| `pnpm dev` | Dev server on http://localhost:5173 (API at `VITE_API_URL`, default http://localhost:8000) |
| `pnpm lint` / `pnpm typecheck` / `pnpm test` / `pnpm build` | The frontend checks (`make fe-check`) |
| `pnpm gen-client` | Regenerates `src/api/schema.d.ts` from `openapi.json` (`make gen-client` exports the schema first) |
| `pnpm e2e` | Playwright smoke specs (`smoke`, `claim`, `vault`, `opportunities`, `matches`, `privacy`; one worker). Needs the API (`make api`), the worker (`make worker`) and a seeded dev database (`make seed`, once). `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` on the cloud VM. The privacy spec seeds one expensive `llm_call` row with `psql` (`E2E_DATABASE_URL`) and removes it. Screenshots go to `e2e/screenshots/` |

Env: `VITE_API_URL`, `VITE_ANALYTICS_ENABLED` (`false` turns analytics off), `VITE_AUTH_MODE`, `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. Vite reads `frontend/.env` (copy the lines from `frontend/.env.example`), not the repository-root `.env`.

## Auth modes

`VITE_AUTH_MODE` selects the client in `src/auth/createAuthClient.ts`. It must match the backend's `AUTH_MODE`.

| Mode | Sign-in | Needs |
|---|---|---|
| `dev` (default) | The e-mail form calls `POST /api/v1/dev/login` on the API and signs in straight away. No password. | Nothing. The API must run with `AUTH_MODE=dev`. |
| `supabase` | "Continue with Google" (OAuth) and an e-mail sign-in link (magic link). | `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. The client refuses to start without them. The API needs `AUTH_MODE=supabase` and `SUPABASE_URL`. |

In dev mode the session (token and e-mail) is kept in `localStorage`. `apiFetch` sends the token as `Authorization: Bearer`. Full details: [docs-site/pages/dev/auth.md](../docs-site/pages/dev/auth.md).

Pages: `/signin` (sign in), `/onboarding` (claim your profile: search, choose the records, confirm), `/` (Home, with the import status) and `/settings/privacy` (consents).

## Private vault (`/vault`, W3 / M3)

`src/features/vault/`: `VaultPage` (page, upload queue), `UploadZone` (drop zone, picker, "Keep the original file" checkbox, unticked by default), `DocumentRow` (status chip, extracted summary and details, kind selector, delete confirm), `status.ts` (chip, failure and error messages, client-side checks: PDF/DOCX, 25 MB), `api.ts` (hooks; upload goes through XHR for progress and is injectable in tests). `GET /documents` is refetched every 2 s while any document is uploaded, extracting, extracted or analysing, and pauses while the tab is hidden. The e2e (`e2e/vault.spec.ts`) needs the API, the worker and the sample PDFs (`cd backend && uv run python ../scripts/make_sample_docs.py`).

## Layout

- `src/routes.ts` route table (patterns are reported as `page` in analytics)
- `src/features/<area>/` feature components (`nav/` header and burger menu, `pages/` placeholder pages)
- `src/ui/` shared pieces (icons, Scout FAB)
- `src/api/` generated schema, `apiFetch` wrapper (adds `X-Session-Id`, `X-Page`)
- `src/analytics/` session, batching client, typed `track()`, page-view router listener, Web Vitals

## Analytics

`page_view` is automatic (`PageViewTracker`). Add feature events with `track('name', {...})` from `src/analytics/client`; names and property keys are typed from `events.ts`, which a test compares against `backend/src/scout/ports/analytics_catalogue.py`. Events batch (10 s, 20 events, page hide via `sendBeacon`), retry once, then drop. The client never throws.
