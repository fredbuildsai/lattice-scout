# Lattice Scout frontend

React 18 + TypeScript (strict) + Vite + Tailwind + React Router + TanStack Query. Design tokens come from `docs/screens/README.md` and live in `tailwind.config.ts`; fonts are self-hosted through `@fontsource/*`.

## Commands (pnpm)

| Command | What it does |
|---|---|
| `pnpm dev` | Dev server on http://localhost:5173 (API at `VITE_API_URL`, default http://localhost:8000) |
| `pnpm lint` / `pnpm typecheck` / `pnpm test` / `pnpm build` | The frontend checks (`make fe-check`) |
| `pnpm gen-client` | Regenerates `src/api/schema.d.ts` from `openapi.json` (`make gen-client` exports the schema first) |
| `pnpm e2e` | Playwright smoke test. Start the API first: `cd backend && uv run uvicorn scout.api.app:create_app --factory --port 8000`. Screenshots go to `e2e/screenshots/` |

Env: `VITE_API_URL`, `VITE_ANALYTICS_ENABLED` (`false` turns analytics off). See `.env.example`.

## Layout

- `src/routes.ts` route table (patterns are reported as `page` in analytics)
- `src/features/<area>/` feature components (`nav/` header and burger menu, `pages/` placeholder pages)
- `src/ui/` shared pieces (icons, Scout FAB)
- `src/api/` generated schema, `apiFetch` wrapper (adds `X-Session-Id`, `X-Page`)
- `src/analytics/` session, batching client, typed `track()`, page-view router listener, Web Vitals

## Analytics

`page_view` is automatic (`PageViewTracker`). Add feature events with `track('name', {...})` from `src/analytics/client`; names and property keys are typed from `events.ts`, which a test compares against `backend/src/scout/ports/analytics_catalogue.py`. Events batch (10 s, 20 events, page hide via `sendBeacon`), retry once, then drop. The client never throws.
