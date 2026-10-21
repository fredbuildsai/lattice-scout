import { expect, test } from '@playwright/test'
import { expectNoHorizontalScroll, SAMPLE_PDF, signInAndClaimFoster, sql } from './helpers'


// M6 main path: delete the vault, a mail preference that persists, the pause banner, delete the account.
// Needs the API and the worker. The pause test inserts one expensive llm_call row (psql, E2E_DATABASE_URL) and
// removes it again; the API caches the budget for 30 s.
test.describe.configure({ mode: 'serial' })

test('delete my vault, keep a mail preference, see the pause banner, delete my account (desktop and 390 px)', async ({ page }) => {
  test.setTimeout(240_000)
  const email = await signInAndClaimFoster(page)

  // vault with one ready document
  await page.getByRole('link', { name: 'Private vault' }).first().click()
  await page.getByLabel('Choose files', { exact: true }).setInputFiles(SAMPLE_PDF)
  await expect(page.getByRole('list', { name: 'Documents' }).getByRole('listitem').first()).toContainText('Ready', { timeout: 90_000 })

  // privacy tab: truthful copy about who reads the text, and what stays
  await page.goto('/settings/privacy')
  await expect(page.getByText('Scout does not train models on your files.')).toBeVisible()
  await expect(page.getByText(/only to providers the operator has approved/)).toBeVisible()
  await expect(page.getByText(/Ratings and comments you gave on a match stay visible/)).toBeVisible()
  await expectNoHorizontalScroll(page)

  // delete my vault: the confirm button waits for the typed word
  await page.getByRole('button', { name: 'Delete my vault' }).click()
  const dialog = page.getByRole('dialog')
  const confirm = dialog.getByRole('button', { name: 'Delete my vault' })
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel('Type DELETE to confirm').fill('DELETE')
  await confirm.click()
  await expect(page.getByRole('status').filter({ hasText: /1/ }).first()).toBeVisible()
  await page.goto('/vault')
  await expect(page.getByText(/Nothing here yet/)).toBeVisible()

  // a mail preference survives a reload
  await page.goto('/settings/privacy')
  const weekly = page.getByLabel('Weekly summary')
  await expect(weekly).not.toBeChecked()
  await weekly.click()
  await expect(weekly).toBeChecked()
  await page.reload()
  await expect(page.getByLabel('Weekly summary')).toBeChecked()

  // 390 px
  await page.setViewportSize({ width: 390, height: 844 })
  await expectNoHorizontalScroll(page)
  await page.screenshot({ path: 'e2e/screenshots/privacy-390.png', fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  // the pause banner appears on the Vault when the day's spend is over the cap
  const inserted = sql(
    "INSERT INTO llm_call (id, ts, feature, agent, route, provider, model, input_tokens, output_tokens, latency_ms, cost_usd, status, prompt_version) " +
      "VALUES ('00000000-0000-4000-8000-00000000e2e1', now(), 'e2e', 'Ping', 'fast', 'fake', 'fake-1', 1, 1, 1, 9000, 'ok', 'e2e')",
  )
  test.skip(!inserted, 'psql is not available: cannot seed spend over the cap')
  try {
    await expect(async () => {
      await page.goto('/vault')
      await expect(page.getByRole('status').filter({ hasText: /daily analysis limit/ }).first()).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 60_000, intervals: [5_000] })
    await page.setViewportSize({ width: 390, height: 844 })
    await expectNoHorizontalScroll(page)
    await page.screenshot({ path: 'e2e/screenshots/pause-banner-390.png', fullPage: true })
    await page.setViewportSize({ width: 1440, height: 900 })
  } finally {
    sql("DELETE FROM llm_call WHERE id = '00000000-0000-4000-8000-00000000e2e1'")
  }

  // delete my account: retype the e-mail, end on the signed-out page, the old token is refused
  const token = await page.evaluate(() => (JSON.parse(localStorage.getItem('scout.session') ?? '{}') as { accessToken?: string }).accessToken ?? '')
  expect(token).not.toBe('')
  await page.goto('/settings/privacy')
  await page.getByRole('button', { name: 'Delete my account' }).click()
  const d2 = page.getByRole('dialog')
  const del = d2.getByRole('button', { name: 'Delete my account' })
  await expect(del).toBeDisabled()
  await d2.getByLabel('Retype your e-mail to confirm').fill(email)
  await del.click()
  await expect(page.getByRole('heading', { name: 'Your account is being deleted' })).toBeVisible()
  const api = process.env.E2E_API_URL ?? 'http://localhost:8000'
  const r = await page.request.get(`${api}/api/v1/me`, { headers: { Authorization: `Bearer ${token}` } })
  expect(r.status()).toBe(403)
})
