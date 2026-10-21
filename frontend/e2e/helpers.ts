import type { Page } from '@playwright/test'

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': '*',
}

/** Pretend to be signed in with a claimed profile, without the backend (shell/navigation tests). */
export async function mockSignedIn(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('scout.session', JSON.stringify({ accessToken: 'e2e', email: 'e2e@example.edu' })))
  const json = (body: unknown) => ({ status: 200, headers: { ...CORS, 'content-type': 'application/json' }, body: JSON.stringify(body) })
  await page.route(/\/api\/v1\/(me|profile)$/, async (route) => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS })
    const isMe = route.request().url().endsWith('/me')
    return route.fulfill(
      json(
        isMe
          ? { account_id: '00000000-0000-0000-0000-000000000001', email: 'e2e@example.edu', role: 'user', launch_field: 'x', created_at: '2026-01-01T00:00:00Z', consent_matching: false, consent_vault_signals: false, researcher: { researcher_id: 'r', display_name: 'E2E User', verified_level: 'self_claimed', import_status: 'done' } }
          : { researcher_id: 'r', display_name: 'E2E User', institution: 'Example University', import: { status: 'done', generation: 1 }, openalex_ids: [], topics: [], works: [], works_summary: { included: 1, total: 1, uncertain: 0 }, verified_level: 'self_claimed' },
      ),
    )
  })
}
