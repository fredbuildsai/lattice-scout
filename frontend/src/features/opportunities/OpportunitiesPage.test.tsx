import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as analytics from '../../analytics/client'
import { OpportunitiesPage, countKinds, filterByKind } from './OpportunitiesPage'
import { card } from './fixtures'

const runDone = {
  id: 'r1',
  status: 'done',
  counts: { corpus: 1, clusters: 1, candidates: 3, drafts: 3, shown: 3 },
  created_at: '2026-10-20T00:00:00Z',
  document_ids: [],
  steps: [],
  degraded: [],
  percent: 100,
  error_code: null,
}
const cards = [card('a', 'resubmission'), card('b', 'white_spot'), card('c', 'white_spot')]

function stub(opts: { runs?: unknown[]; items?: unknown[] } = {}) {
  const calls: { url: string; method: string; body?: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body })
      if (url.includes('/runs'))
        return new Response(JSON.stringify({ items: opts.runs ?? [runDone] }), {
          status: 200,
        })
      if (url.includes('/feedback') && !init?.method) {
        return url.includes('target_id=a')
          ? new Response(
              JSON.stringify({
                target_type: 'opportunity',
                target_id: 'a',
                rating: 'down',
                reasons: [],
                comment: null,
                stars: null,
                updated_at: 'x',
              }),
              { status: 200 },
            )
          : new Response('{}', { status: 404 })
      }
      if (init?.method === 'PATCH')
        return new Response(JSON.stringify(card('a', 'resubmission', { status: 'dismissed' })), { status: 200 })
      return new Response(JSON.stringify({ items: opts.items ?? cards, next_cursor: null }), { status: 200 })
    }),
  )
  return calls
}
function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <OpportunitiesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('filter helpers', () => {
  it('filters and counts by kind', () => {
    expect(filterByKind(cards, 'white_spot').map((c) => c.id)).toEqual(['b', 'c'])
    expect(filterByKind(cards, 'all')).toHaveLength(3)
    expect(countKinds(cards)).toEqual({ resubmission: 1, white_spot: 2 })
  })
})

describe('OpportunitiesPage', () => {
  it('notes on a card that it rests on published work only', async () => {
    const note = 'Based on your published work only; your uploaded documents were not used because no approved provider is configured.'
    stub({ items: [card('a', 'white_spot', { basis: 'published_only', basis_note: note }), card('b')] })
    renderPage()
    const list = await screen.findByRole('list', { name: 'Opportunities' })
    expect(within(list).getAllByText(note)).toHaveLength(1) // only the card that needs it
  })

  it('prefills the thumbs from GET /feedback', async () => {
    stub()
    renderPage()
    const down = await screen.findAllByRole('button', {
      name: 'Not useful: thumbs down',
    })
    await vi.waitFor(() => expect(down[0]).toHaveAttribute('aria-pressed', 'true'))
    expect(down[1]).toHaveAttribute('aria-pressed', 'false')
  })

  it('lists cards in server order with kind counts and filters by kind', async () => {
    stub()
    const user = userEvent.setup()
    renderPage()
    const list = await screen.findByRole('list', { name: 'Opportunities' })
    expect(
      within(list)
        .getAllByRole('heading', { level: 3 })
        .map((h) => h.textContent),
    ).toEqual(['Idea a', 'Idea b', 'Idea c'])
    expect(screen.getByRole('button', { name: /White spot\s*2/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /White spot/ }))
    expect(
      within(screen.getByRole('list', { name: 'Opportunities' })).getAllByRole('heading', { level: 3 }),
    ).toHaveLength(2)
    expect(screen.getByRole('img', { name: /Scatter chart of 2 ideas/ })).toBeInTheDocument()
  })

  it('requests dismissed ideas when the status radio changes', async () => {
    const calls = stub({ items: [] })
    const user = userEvent.setup()
    renderPage()
    await screen.findByText(/Scout did not find|none had enough|No analysis/)
    await user.click(screen.getByRole('radio', { name: 'Dismissed' }))
    expect(await screen.findByText('You have not dismissed any ideas.')).toBeInTheDocument()
    expect(calls.some((c) => c.url.includes('status=dismissed'))).toBe(true)
  })

  it('dismisses with a PATCH and tracks it; opening tracks the position', async () => {
    const calls = stub()
    const track = vi.spyOn(analytics, 'track').mockImplementation(() => undefined)
    const user = userEvent.setup()
    renderPage()
    const first = await screen.findByRole('listitem', { name: 'Idea a' })
    await user.click(within(first).getByRole('link', { name: 'Open' }))
    expect(track).toHaveBeenCalledWith('opportunity_opened', {
      opportunity_id: 'a',
      position: 1,
    })
    await user.click(within(first).getByRole('button', { name: 'Dismiss' }))
    await vi.waitFor(() =>
      expect(track).toHaveBeenCalledWith('opportunity_dismissed', {
        opportunity_id: 'a',
      }),
    )
    expect(calls.find((c) => c.method === 'PATCH')?.body).toBe(JSON.stringify({ status: 'dismissed' }))
  })

  it('shows the run reason when nothing was shown', async () => {
    stub({
      runs: [{ ...runDone, counts: { ...runDone.counts, shown: 0 } }],
      items: [],
    })
    renderPage()
    expect(await screen.findByText(/none had enough verified evidence/)).toBeInTheDocument()
  })

  it('shows the no-run empty state', async () => {
    stub({ runs: [] })
    renderPage()
    expect(await screen.findByText(/No analysis yet/)).toBeInTheDocument()
  })
})
