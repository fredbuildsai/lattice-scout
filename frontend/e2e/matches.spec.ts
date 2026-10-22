import { expect, test, type Browser } from '@playwright/test'
import { expectNoHorizontalScroll, signIn } from './helpers'

// M5: the demo pair (Johan Foster and Raphaell Moreira, public OpenAlex records) from `make seed`.
// Needs: make seed (once), the API and the worker. The seed is idempotent; a re-run finds the pair already mutual
// and the test then only checks the revealed card.
const FOSTER = 'johan.foster@example.edu'
const MOREIRA = 'r.moreira@example.edu'

async function open(browser: Browser, email: string, width = 1440) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 900 } })
  const page = await ctx.newPage()
  await signIn(page, email)
  await page.goto('/matches')
  await expect(page.getByRole('heading', { level: 1, name: 'Matches' })).toBeVisible()
  return { ctx, page }
}

test('both sides accept, the reveal job runs, both see contact and joint ideas, then rate', async ({ browser }) => {
  test.setTimeout(180_000)
  const a = await open(browser, FOSTER)
  const b = await open(browser, MOREIRA)

  // Moreira has exactly one proposal, with Foster; Foster has several, so pick the same one by id
  const id = (await b.page.locator('li[data-match-id]').first().getAttribute('data-match-id'))!
  expect(id).toMatch(/^[0-9a-f-]{36}$/)
  await a.page.locator(`li[data-match-id="${id}"] button`).click()

  // before the reveal a card is anonymous: no name, no e-mail anywhere on the page
  const already = await a.page.getByRole('article', { name: /^Mutual match/ }).count()
  if (!already) {
    const anon = a.page.getByRole('article', { name: /^Anonymous match/ })
    await expect(anon).toBeVisible()
    const text = (await a.page.locator('main').innerText()).toLowerCase()
    expect(text).not.toContain('moreira')
    expect(text).not.toContain('example.edu')
    await expectNoHorizontalScroll(a.page)

    await a.page.getByRole('button', { name: 'Accept' }).click()
    await expect(a.page.getByText(/Waiting for the other researcher/)).toBeVisible()
    await b.page.reload()
    await b.page.getByRole('button', { name: 'Accept' }).click()
  }

  for (const { page, who } of [{ page: a.page, who: 'Raphaell Moreira' }, { page: b.page, who: 'Johan Foster' }]) {
    await page.reload()
    await page.locator(`li[data-match-id="${id}"] button`).click()
    const mutual = page.getByRole('article', { name: /^Mutual match/ })
    await expect(mutual).toBeVisible({ timeout: 30_000 })
    await expect(mutual).toContainText(who)
    await expect(mutual).toContainText('example.edu')
    // joint ideas arrive when the reveal job has run
    await expect(page.getByRole('list', { name: 'Joint opportunities' })).toBeVisible({ timeout: 90_000 })
  }

  // rate the mutual match
  await a.page.getByRole('button', { name: '4 stars' }).click()
  await a.page.getByRole('button', { name: 'Methods fit' }).click()
  await a.page.getByRole('button', { name: 'Send feedback' }).click()
  await expect(a.page.getByText(/your feedback is saved/)).toBeVisible()
  await a.page.screenshot({ path: 'e2e/screenshots/matches-1440.png', fullPage: true })
  await a.ctx.close()
  await b.ctx.close()
})

test('the same pages at 390 px do not scroll sideways', async ({ browser }) => {
  const m = await open(browser, FOSTER, 390)
  await expectNoHorizontalScroll(m.page)
  await m.page.screenshot({ path: 'e2e/screenshots/matches-390.png', fullPage: true })
  await m.ctx.close()
})
