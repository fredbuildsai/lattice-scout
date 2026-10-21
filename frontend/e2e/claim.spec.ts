import { expect, test } from '@playwright/test'

// Needs the API and the worker: see frontend/README.md (M1). Unique e-mail per run keeps state fresh.
test('dev login, claim Johan Foster, import, land on Home', async ({ page }) => {
  const email = `e2e-${Date.now()}@example.edu`
  await page.goto('/')
  await expect(page).toHaveURL(/\/signin$/)
  await expect(page.getByRole('button', { name: /ORCID/ })).toHaveCount(0)
  await page.screenshot({ path: 'e2e/screenshots/signin-1440.png' })
  await page.getByLabel('Work email').fill(email)
  await page.getByRole('button', { name: 'Continue' }).click()

  await expect(page).toHaveURL(/\/onboarding$/)
  await expect(page.getByLabel(/Your name/)).toHaveValue('')
  await page.getByLabel(/Your name/).fill('Johan Foster')
  await page.getByRole('button', { name: 'Search' }).click()

  const card = page.getByRole('list', { name: 'Candidate profiles' }).getByRole('listitem').filter({ hasText: 'includes 2 records' }).first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await expect(card).toContainText('University of British Columbia')
  await card.getByRole('button', { name: /This is me/ }).click()
  await expect(card).toContainText('159 works')
  const boxes = card.getByRole('checkbox')
  await expect(boxes.first()).toBeChecked()
  await expect(page.getByLabel('Suggest me to complementary researchers')).toBeVisible()
  await page.screenshot({ path: 'e2e/screenshots/onboarding-1440.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'e2e/screenshots/onboarding-390.png', fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.getByRole('button', { name: 'Confirm' }).click()

  // Works confirmation: uncertain works are unticked, toggling changes the summary.
  await expect(page.getByRole('heading', { name: 'Publications & outputs' })).toBeVisible({ timeout: 60_000 })
  const summary = page.getByLabel('Works summary')
  await expect(summary).toContainText('149 of 159 included · 10 uncertain')
  const works = page.getByRole('list', { name: 'Works' })
  await expect(works.getByRole('checkbox').first()).toBeVisible()
  await expect(works.getByText('Not an article').first()).toBeVisible()
  await expect(page.getByText(/\b0 citations/)).toHaveCount(0)
  const before = Number(/(\d+) of/.exec((await summary.textContent()) ?? '')?.[1])
  const first = works.getByRole('checkbox').nth(10) // the 10 uncertain works are listed first
  await expect(first).toBeChecked()
  await first.click()
  await expect(first).not.toBeChecked()
  await expect(summary).toContainText(`${before - 1} of`)
  await page.reload()
  await expect(page.getByLabel('Works summary')).toContainText(`${before - 1} of`)
  await page.getByRole('list', { name: 'Works' }).getByRole('checkbox').nth(10).click()
  await expect(page.getByLabel('Works summary')).toContainText(`${before} of`)
  await page.screenshot({ path: 'e2e/screenshots/works-1440.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'e2e/screenshots/works-390.png', fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.getByRole('link', { name: 'Continue to Home' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible()
  await expect(page.getByLabel('Profile summary')).toContainText(/works included/)
  await page.screenshot({ path: 'e2e/screenshots/home-claimed-1440.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: 'e2e/screenshots/home-claimed-390.png', fullPage: true })

  // Home links back to the list.
  await page.getByRole('link', { name: 'Review my publications' }).click()
  await expect(page).toHaveURL(/\/onboarding\?step=works/)
  await expect(page.getByRole('heading', { name: 'Publications & outputs' })).toBeVisible()
})
