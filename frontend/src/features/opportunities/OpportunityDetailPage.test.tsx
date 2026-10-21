import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OpportunityDetailPage, groupEvidence } from './OpportunityDetailPage'
import { claim, count, lit, vault } from './fixtures'
import type { OpportunityDetail } from './api'

const detail = (over: Partial<OpportunityDetail> = {}): OpportunityDetail => ({
  id: 'o1', run_id: 'r1', kind: 'white_spot', title: 'Lignin foams without isocyanates', status: 'shown', verified_sources: 4, clusters: ['Lignin'],
  created_at: '2026-10-20T00:00:00Z', effort_estimate: '3 months', why: null,
  rationale: [claim({ id: 'c1', markers: [lit, vault] })], objection: [claim({ id: 'c2', text: 'Fire safety is open.', markers: [vault, count] })], score_claims: [],
  novelty: { count: 141, since_year: 2021, query: 'lignin AND foam', band: 'crowded' }, scores: { fit: 0.8, novelty: 0.4, readiness: 0.6, effort: 0.3 },
  readiness: [{ item: 'Preliminary data', present: true }, { item: 'Budget', present: false }], next_steps: ['Ask a partner', 'Draft a one-pager'], ...over,
})

function stub(opts: { detail?: OpportunityDetail; fail?: boolean } = {}) {
  const calls: { url: string; method: string; body?: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url, method, body: init?.body })
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status })
      if (url.includes('/feedback')) return json({}, 404)
      if (opts.fail) return json({}, 404)
      if (method === 'PATCH') return json({ ...detail(), status: JSON.parse(String(init?.body)).status })
      return json(opts.detail ?? detail())
    }),
  )
  return calls
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/opportunities/o1']}>
        <Routes>
          <Route path="/opportunities/:id" element={<OpportunityDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => localStorage.clear())
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('groupEvidence', () => {
  it('lists each evidence item once, grouped by source type, without count items', () => {
    const g = groupEvidence(detail())
    expect(g.vault!.map((e) => e.id)).toEqual(['e2']) // cited by two claims, listed once
    expect(g.literature!.map((e) => e.id)).toEqual(['e1'])
    expect(Object.keys(g)).not.toContain('count')
  })
})

describe('OpportunityDetailPage', () => {
  it('shows the claims, the evidence tabs with counts, the measured count, the scores and the readiness checklist', async () => {
    stub()
    renderPage()
    expect(await screen.findByRole('heading', { name: 'Lignin foams without isocyanates' })).toBeInTheDocument()
    const tabs = screen.getByRole('tablist', { name: 'Evidence by source' })
    expect(within(tabs).getByRole('tab', { name: /Your private vault · 1/ })).toBeInTheDocument()
    expect(within(tabs).getByRole('tab', { name: /Literature · 1/ })).toBeInTheDocument()
    expect(within(tabs).getByRole('tab', { name: /Your published work · 0/ })).toBeInTheDocument()
    // the first non-empty tab is open; another tab can be chosen
    expect(screen.getByText('proposal.pdf')).toBeInTheDocument()
    await userEvent.click(within(tabs).getByRole('tab', { name: /Your published work/ }))
    expect(screen.getByText('Nothing cited from this source.')).toBeInTheDocument()
    expect(screen.getByText('141')).toBeInTheDocument()
    expect(screen.getByText('crowded')).toBeInTheDocument()
    const checklist = screen.getByText('Readiness checklist').parentElement as HTMLElement
    expect(within(checklist).getByText(/Preliminary data/)).toBeInTheDocument()
    expect(within(checklist).getByText('(missing)')).toBeInTheDocument()
  })

  it('shows "No measured count" when OpenAlex gave none', async () => {
    stub({ detail: detail({ novelty: { count: null, since_year: null, query: null, band: null } }) })
    renderPage()
    expect(await screen.findByText('No measured count for this idea.')).toBeInTheDocument()
  })

  it('saves, dismisses and restores through PATCH', async () => {
    const calls = stub()
    renderPage()
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    const patches = calls.filter((c) => c.method === 'PATCH').map((c) => JSON.parse(String(c.body)))
    expect(patches).toEqual([{ status: 'saved' }, { status: 'dismissed' }])
  })

  it('offers Restore for a dismissed idea', async () => {
    const calls = stub({ detail: detail({ status: 'dismissed' }) })
    renderPage()
    await userEvent.click(await screen.findByRole('button', { name: 'Restore' }))
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => JSON.parse(String(c.body)))).toEqual([{ status: 'shown' }])
  })

  it('keeps the next-steps ticks in this browser', async () => {
    stub()
    renderPage()
    const user = userEvent.setup()
    const box = await screen.findByRole('checkbox', { name: 'Ask a partner' })
    await user.click(box)
    expect(box).toBeChecked()
    expect(JSON.parse(localStorage.getItem('scout.nextsteps.o1') ?? '[]')).toEqual([0])
  })

  it('says so when the opportunity cannot be loaded', async () => {
    stub({ fail: true })
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent('We could not load this opportunity')
  })
})
