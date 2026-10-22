import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunPage } from './RunPage'
import { STEP_LABELS } from './messages'

const NAMES = Object.keys(STEP_LABELS)
const run = (over: Record<string, unknown> = {}) => ({
  id: 'r1', status: 'running', phase: 'retrieve', step: 'retrieve', percent: 35, partial: false, degraded: [], error_code: null, document_ids: ['d1', 'd2'],
  counts: { corpus: 120, clusters: 5, candidates: 4, drafts: 3, shown: 2 },
  steps: NAMES.map((name, i) => ({ name, status: i < 2 ? 'done' : i === 2 ? 'running' : 'pending', progress: i === 2 ? 0.5 : 0 })), created_at: '2026-10-20T00:00:00Z', ...over,
})

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/runs/r1']}>
        <Routes>
          <Route path="/runs/:id" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
afterEach(() => vi.unstubAllGlobals())

describe('RunPage', () => {
  it('shows 8 steps, the progress bar and cancels', async () => {
    const calls: string[] = []
    let cancelled = false
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      if (init?.method === 'POST') cancelled = true
      return new Response(JSON.stringify(cancelled ? run({ status: 'cancelled' }) : run()), { status: 200 })
    }))
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Scout is analysing 2 documents' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '35')
    expect(screen.getAllByRole('listitem')).toHaveLength(8)
    await user.click(screen.getByRole('button', { name: 'Cancel run' }))
    expect(await screen.findByRole('heading', { name: 'Analysis cancelled' })).toBeInTheDocument()
    expect(calls.some((c) => c.startsWith('POST') && c.endsWith('/runs/r1/cancel'))).toBe(true)
  })

  it('links to results when done and notes degraded steps', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(run({ status: 'done', percent: 100, degraded: [{ step: 'retrieve', reason: 'OpenAlex was slow' }] })), { status: 200 })))
    renderPage()
    expect(await screen.findByRole('link', { name: 'See the opportunities' })).toHaveAttribute('href', '/opportunities?run=r1')
    expect(screen.getByRole('note')).toHaveTextContent('OpenAlex was slow')
  })

  it('says honestly when the run used published work only, and stays quiet otherwise', async () => {
    const note = 'Based on your published work only; your uploaded documents were not used because no approved provider is configured.'
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(run({ status: 'done', percent: 100, basis: 'published_only', basis_note: note })), { status: 200 })))
    const view = renderPage()
    expect(await screen.findByText(note)).toBeInTheDocument()
    view.unmount()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(run({ status: 'done', percent: 100, basis: 'full', basis_note: null })), { status: 200 })))
    renderPage()
    await screen.findByRole('link', { name: 'See the opportunities' })
    expect(screen.queryByText(/published work only/)).toBeNull()
  })

  it('explains a failed run in plain words', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(run({ status: 'failed', error_code: 'corpus_empty' })), { status: 200 })))
    renderPage()
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/could not find enough papers/))
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull()
  })
})
