import { expect, test } from '@playwright/test'
import { expectNoHorizontalScroll, signInAndClaimFoster, uploadFosterSamples } from './helpers'

// M4 main path (plan M4 acceptance 1): upload -> "Analyse with Scout" -> run page -> opportunity list -> detail ->
// source marker -> "Open passage" -> rate. Needs the API, the worker (fake LLM + fixtures) and the sample PDFs.
test('upload, analyse, open an opportunity, check its sources and rate it; then the same page at 390 px', async ({ page }) => {
  test.setTimeout(240_000)
  await signInAndClaimFoster(page)

  await page.getByRole('link', { name: 'Private vault' }).first().click()
  await uploadFosterSamples(page)

  await page.getByRole('button', { name: /Analyse with Scout/ }).click()
  await expect(page).toHaveURL(/\/runs\/[0-9a-f-]+$/)
  await expect(page.getByRole('heading', { name: 'Analysis finished' })).toBeVisible({ timeout: 180_000 })
  await page.getByRole('link', { name: 'See the opportunities' }).click()

  const cards = page.getByRole('listitem').filter({ has: page.getByRole('link', { name: 'Open', exact: true }) })
  await expect(cards.first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Where the ideas sit' })).toBeVisible()
  await expectNoHorizontalScroll(page)
  await cards.first().getByRole('link', { name: 'Open', exact: true }).click()

  // detail: claims carry numbered source markers; every one opens a dialog with its source
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  const marker = page.getByRole('button', { name: /^Source 1:/ }).first()
  await marker.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // evidence tabs: the vault tab offers the passage; "Open passage" shows it or the saved quote
  const tabs = page.getByRole('tablist', { name: 'Evidence by source' })
  await tabs.getByRole('tab', { name: /Your private vault/ }).click()
  const open = page.getByRole('button', { name: 'Open passage' }).first()
  if (await open.count()) {
    await open.click()
    await expect(page.getByText(/Original file not kept|^Loading the passage/).or(page.locator('p.whitespace-pre-wrap')).first()).toBeVisible()
  }

  // rate: thumbs up with a reason, then reload and see it again
  await page.getByRole('button', { name: 'Spot on' }).click()
  await page.getByRole('button', { name: /thumbs up/ }).click()
  await expect(page.getByText(/feedback is saved/)).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: /thumbs up/ })).toHaveAttribute('aria-pressed', 'true')
  await page.screenshot({ path: 'e2e/screenshots/opportunity-1440.png', fullPage: true })

  // 390 px: no sideways scroll, and the marker opens as a bottom sheet
  await page.setViewportSize({ width: 390, height: 844 })
  await expectNoHorizontalScroll(page)
  await page.getByRole('button', { name: /^Source 1:/ }).first().click()
  const sheet = page.getByRole('dialog')
  await expect(sheet).toBeVisible()
  const box = (await sheet.boundingBox())!
  expect(box.x).toBeLessThanOrEqual(1)
  expect(box.width).toBeGreaterThanOrEqual(388)
  expect(box.y + box.height).toBeGreaterThanOrEqual(843) // sits at the bottom edge
  await page.screenshot({ path: 'e2e/screenshots/opportunity-390.png', fullPage: false })
  await page.keyboard.press('Escape')
  await page.goto('/opportunities')
  await expectNoHorizontalScroll(page)
})
