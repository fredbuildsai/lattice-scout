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
  await expect(page).toHaveURL(/\/$/, { timeout: 60_000 })
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible()
  await expect(page.getByLabel('Profile summary')).toContainText(/works included/)
  await page.screenshot({ path: 'e2e/screenshots/home-claimed-1440.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: 'e2e/screenshots/home-claimed-390.png', fullPage: true })
})
