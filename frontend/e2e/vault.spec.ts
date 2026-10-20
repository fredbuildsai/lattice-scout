import { expect, test } from '@playwright/test'
import { resolve } from 'node:path'

// Needs the API and the worker (fake LLM): see frontend/README.md. The file name must stay as is,
// the fake LLM fixture is keyed on it. Build it with: cd backend && uv run python ../scripts/make_sample_docs.py
const PDF = resolve(process.cwd(), '../tests/fixtures/vault/build/declined-proposal-lignin-foams.pdf')

test('upload a PDF, wait for Ready, read insights, change the kind, delete', async ({ page }) => {
  await page.goto('/signin')
  await page.getByLabel('Work email').fill(`e2e-vault-${Date.now()}@example.edu`)
  await page.getByRole('button', { name: 'Continue' }).click()
  // A vault needs a claimed profile: claim Johan Foster as in claim.spec.ts.
  await expect(page).toHaveURL(/\/onboarding$/)
  await page.getByLabel(/Your name/).fill('Johan Foster')
  await page.getByRole('button', { name: 'Search' }).click()
  const card = page.getByRole('list', { name: 'Candidate profiles' }).getByRole('listitem').filter({ hasText: 'includes 2 records' }).first()
  await expect(card).toBeVisible({ timeout: 30_000 })
  await card.getByRole('button', { name: /This is me/ }).click()
  await page.getByRole('button', { name: 'Confirm' }).click()
  await expect(page.getByRole('heading', { name: 'Publications & outputs' })).toBeVisible({ timeout: 60_000 })
  await page.getByRole('link', { name: 'Private vault' }).first().click()
  await expect(page).toHaveURL(/\/vault$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Private vault' })).toBeVisible()
  await expect(page.getByLabel(/Keep the original file/)).not.toBeChecked()
  await expect(page.getByText(/Nothing here yet/)).toBeVisible()

  await page.getByLabel('Choose files', { exact: true }).setInputFiles(PDF)
  const doc = page.getByRole('list', { name: 'Documents' }).getByRole('listitem').first()
  await expect(doc).toContainText('declined-proposal-lignin-foams.pdf', { timeout: 15_000 })
  await expect(doc).toContainText('Ready', { timeout: 60_000 })
  await expect(doc).toContainText(/What Scout extracted: \d+ aims?/)
  await doc.getByRole('button', { name: 'Show details' }).click()
  await expect(doc.getByRole('heading', { name: 'Reviewer objections' })).toBeVisible()
  await expect(doc.locator('blockquote').first()).toBeVisible()

  await page.screenshot({ path: 'e2e/screenshots/vault-1440.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'e2e/screenshots/vault-390.png', fullPage: true })
  await page.setViewportSize({ width: 1440, height: 900 })

  // Same file again: "Already in your vault".
  await page.getByLabel('Choose files', { exact: true }).setInputFiles(PDF)
  await expect(page.getByText('Already in your vault')).toBeVisible()

  const select = doc.getByLabel('Scout thinks this is…')
  await select.selectOption('proposal')
  await expect(doc.getByText(/You chose this/)).toBeVisible()
  await page.reload()
  await expect(page.getByLabel('Scout thinks this is…')).toHaveValue('proposal')

  await page.getByRole('button', { name: /^Delete declined-proposal/ }).click()
  await expect(page.getByRole('dialog')).toContainText('cannot be undone')
  await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText(/Nothing here yet/)).toBeVisible()
})
