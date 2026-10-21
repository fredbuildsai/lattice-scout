import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../../api/client'
import { PausedBanner } from './PausedBanner'
import { startRunErrorMessage } from './messages'

function renderBanner(status: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(status), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <PausedBanner />
      <p>page</p>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('PausedBanner', () => {
  it('shows the server message when runs are paused', async () => {
    renderBanner({ accepting_runs: false, message: 'Paused until midnight UTC.' })
    expect(await screen.findByText('Paused until midnight UTC.')).toBeInTheDocument()
  })

  it('renders nothing when runs are accepted', async () => {
    renderBanner({ accepting_runs: true, message: '' })
    await screen.findByText('page')
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('startRunErrorMessage', () => {
  it('maps service_paused and upload_limit_reached', () => {
    expect(startRunErrorMessage(new ApiError(503, '/runs', 'x', 'service_paused'))).toMatch(/paused for today/)
    expect(startRunErrorMessage(new ApiError(429, '/runs', 'x', 'upload_limit_reached'))).toMatch(/limit/)
  })
  it('falls back to the detail, then to a generic message', () => {
    expect(startRunErrorMessage(new ApiError(400, '/runs', 'Pick documents'))).toBe('Pick documents')
    expect(startRunErrorMessage(new Error('x'))).toMatch(/could not start/)
  })
})
