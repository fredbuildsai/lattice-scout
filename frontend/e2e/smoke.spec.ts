import { expect, test, type Page, type Request } from '@playwright/test'

interface Batch {
  session: { id: string }
  events: Array<{ name: string; page: string; prev_page: string | null; seq: number }>
}

async function viaMenu(page: Page, name: RegExp) {
  await page.getByRole('button', { name: 'Open menu' }).click()
  await page.getByRole('navigation', { name: 'All destinations' }).getByRole('link', { name }).click()
}

test('navigates five pages through the menu and reports page_view events', async ({ page }) => {
  const batches: Batch[] = []
  const statuses: number[] = []
  page.on('request', (r: Request) => {
    if (r.method() === 'POST' && r.url().endsWith('/api/v1/events')) {
      batches.push(JSON.parse(r.postData() ?? '{"events":[]}') as Batch)
    }
  })
  page.on('response', (r) => {
    if (r.url().endsWith('/api/v1/events')) statuses.push(r.status())
  })

  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible()
  await page.screenshot({ path: 'e2e/screenshots/home-1440.png', fullPage: true })

  await viaMenu(page, /Private vault/)
  await expect(page.getByRole('heading', { level: 1, name: 'Private vault' })).toBeVisible()
  await viaMenu(page, /Opportunities/)
  await expect(page.getByRole('heading', { level: 1, name: 'Opportunities' })).toBeVisible()
  await viaMenu(page, /Matches/)
  await expect(page.getByRole('heading', { level: 1, name: 'Matches' })).toBeVisible()
  await viaMenu(page, /Profile and privacy/)
  await expect(page.getByRole('heading', { level: 1, name: 'Profile and privacy' })).toBeVisible()

  await page.getByRole('button', { name: 'Open menu' }).click()
  await page.screenshot({ path: 'e2e/screenshots/menu-1440.png' })

  // The 10 s timer flush (sendBeacon on unload is covered by unit tests: Playwright cannot read beacon bodies).
  await expect
    .poll(() => batches.flatMap((b) => b.events).filter((e) => e.name === 'page_view').length, { timeout: 20_000 })
    .toBeGreaterThanOrEqual(5)
  const events = batches.flatMap((b) => b.events)
  const views = events.filter((e) => e.name === 'page_view')
  expect(views.slice(0, 5).map((e) => [e.page, e.prev_page])).toEqual([
    ['/', null],
    ['/vault', '/'],
    ['/opportunities', '/vault'],
    ['/matches', '/opportunities'],
    ['/settings/privacy', '/matches'],
  ])
  const seqs = events.map((e) => e.seq)
  expect(seqs).toEqual([...seqs].sort((a, b) => a - b))
  expect(new Set(batches.map((b) => b.session.id)).size).toBe(1)
  expect(events.some((e) => e.name === 'menu_opened')).toBe(true)
  expect(events.some((e) => e.name === 'menu_item_clicked')).toBe(true)
  for (const s of statuses) expect(s).toBe(202)
})

test('mobile layout at 390 px: no horizontal scroll, menu keyboard accessible', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/matches')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'e2e/screenshots/matches-390.png' })

  await page.getByRole('button', { name: 'Open menu' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('navigation', { name: 'All destinations' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Close menu' }).first()).toBeFocused()
  await page.screenshot({ path: 'e2e/screenshots/menu-390.png' })
  await page.keyboard.press('Escape')
  await expect(page.getByRole('navigation', { name: 'All destinations' })).toBeHidden()
  await expect(page.getByRole('button', { name: 'Open menu' })).toBeFocused()
})
