import { expect, type Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': '*',
}

/** Pretend to be signed in with a claimed profile, without the backend (shell/navigation tests). */
export async function mockSignedIn(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('scout.session', JSON.stringify({ accessToken: 'e2e', email: 'e2e@example.edu' })))
  // Every other API call fails like an unreachable server instead of answering 401, which would sign the fake user out.
  await page.route(/\/api\/v1\/(?!(me|profile|events)$)/, (route) => route.abort())
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

/** Sample PDF the fake LLM knows (file name must stay). Build: cd backend && uv run python ../scripts/make_sample_docs.py */
export const SAMPLE_PDF = resolve(process.cwd(), '../tests/fixtures/vault/build/declined-proposal-lignin-foams.pdf')

/** Dev sign-in (AUTH_MODE=dev) with a fresh or given address; ends on the page after sign-in. */
export async function signIn(page: Page, email: string): Promise<void> {
  await page.goto('/signin')
  await page.getByLabel('Work email').fill(email)
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/signin'))
}

/** Sign in as a new user and claim Johan Foster's public OpenAlex records (fixtures), as in claim.spec.ts. */
export async function signInAndClaimFoster(page: Page, email = `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.edu`): Promise<string> {
  await signIn(page, email)
  await expect(page).toHaveURL(/\/onboarding$/)
  await page.getByLabel(/Your name/).fill('Johan Foster')
  await page.getByRole('button', { name: 'Search' }).click()
  const card = page.getByRole('list', { name: 'Candidate profiles' }).getByRole('listitem').filter({ hasText: 'includes 2 records' }).first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: /This is me/ }).click()
  await page.getByRole('button', { name: 'Confirm' }).click()
  await expect(page.getByRole('heading', { name: 'Publications & outputs' })).toBeVisible({ timeout: 60_000 })
  return email
}

/** The page never scrolls sideways. */
export async function expectNoHorizontalScroll(page: Page): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
}

/** Run SQL on the dev database (E2E_DATABASE_URL); returns false when psql is not available. */
export function sql(statement: string): boolean {
  const url = process.env.E2E_DATABASE_URL ?? 'postgresql://scout:scout@localhost:5432/scout'
  try {
    execFileSync('psql', [url, '-v', 'ON_ERROR_STOP=1', '-q', '-c', statement], { stdio: 'pipe' })
    return true
  } catch {
    return false
  }
}

const SAMPLES = [
  { file: 'declined-proposal-lignin-foams.pdf', keep: true },
  { file: 'unpublished-results-cnf-membrane.pdf', keep: false },
  { file: 'lab-notes-pfas-membrane.pdf', keep: false },
]

/** Upload the three fictional sample documents of the Foster eval case (fixture keys depend on name and retention),
 * wait until each is Ready, then tick them in the order the fixtures expect (d1, d2, d3). */
export async function uploadFosterSamples(page: Page): Promise<void> {
  const dir = resolve(process.cwd(), '../tests/fixtures/vault/build')
  for (const [i, s] of SAMPLES.entries()) {
    const keep = page.getByLabel(/Keep the original file/)
    if (s.keep) await keep.check()
    else await keep.uncheck()
    await page.getByLabel('Choose files', { exact: true }).setInputFiles(resolve(dir, s.file))
    const row = page.getByRole('list', { name: 'Documents' }).getByRole('listitem').filter({ hasText: s.file })
    await expect(row).toContainText('Ready', { timeout: 90_000 })
    expect(await page.getByRole('list', { name: 'Documents' }).getByRole('listitem').count()).toBe(i + 1)
  }
  for (const s of SAMPLES) await page.getByRole('checkbox', { name: `Select ${s.file} for analysis` }).check()
}
