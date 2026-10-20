import { existsSync } from 'node:fs'
import { defineConfig } from '@playwright/test'

// The VM ships a Chromium build that does not match this Playwright version; use it when present.
const local = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: {
    viewport: { width: 1440, height: 900 },
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    launchOptions: existsSync(local) ? { executablePath: local } : {},
  },
  // Start the API separately: cd backend && uv run uvicorn scout.api.app:create_app --factory --port 8000
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
  },
})
