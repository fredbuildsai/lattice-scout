import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HomePage } from './HomePage'

const profile = {
  researcher_id: 'r1',
  display_name: 'Ada Test',
  institution: 'Example University',
  country: 'DE',
  orcid: null,
  verified_level: 'self_claimed',
  openalex_ids: [],
  import: { status: 'done' },
  works: [],
  works_summary: { included: 3, total: 4, uncertain: 1 },
  topics: [
    { topic_id: 'T1', name: 'PFAS removal', subfield: null, weight: 1 },
    { topic_id: 'T2', name: null, subfield: null, weight: 0.5 },
  ],
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('HomePage', () => {
  it('shows the profile summary with a link back to the works review', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        const json = (b: unknown) => new Response(JSON.stringify(b))
        if (url.includes('/api/v1/profile')) return json(profile)
        if (url.includes('/api/v1/runs')) return json({ items: [] })
        if (url.includes('/api/v1/documents')) return json({ items: [], next_cursor: null })
        if (url.includes('/api/v1/matches'))
          return json({
            matching_enabled: false,
            items: [],
            next_cursor: null,
          })
        return new Response('{}', { status: 404 })
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter>
          <HomePage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(await screen.findByText('3 of 4 works included')).toBeInTheDocument()
    expect(screen.getByText('Example University · DE')).toBeInTheDocument()
    expect(screen.getByText('PFAS removal')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review my publications' })).toHaveAttribute(
      'href',
      '/onboarding?step=works',
    )
  })

  describe('Scout’s briefing', () => {
    const base = { id: 'r1', created_at: '2026-10-20T00:00:00Z', document_ids: ['d1', 'd2'], steps: [], degraded: [], percent: 40, error_code: null }
    const counts = { corpus: 120, clusters: 3, candidates: 5, drafts: 4, shown: 2 }
    async function briefingFor(runs: unknown[]) {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (input: RequestInfo | URL) => {
          const url = String(input)
          const json = (b: unknown) => new Response(JSON.stringify(b))
          if (url.includes('/api/v1/profile')) return json(profile)
          if (url.includes('/api/v1/runs')) return json({ items: runs })
          if (url.includes('/api/v1/documents')) return json({ items: [], next_cursor: null })
          if (url.includes('/api/v1/matches')) return json({ matching_enabled: false, items: [], next_cursor: null })
          if (url.includes('/api/v1/opportunities')) return json({ items: [], next_cursor: null })
          return new Response('{}', { status: 404 })
        }),
      )
      render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter>
            <HomePage />
          </MemoryRouter>
        </QueryClientProvider>,
      )
      return screen.findByRole('region', { name: 'Scout’s briefing' })
    }

    it('invites the first analysis when there is no run', async () => {
      const region = await briefingFor([])
      await waitFor(() => expect(region).toHaveTextContent('I have not analysed anything yet'))
      expect(screen.getByRole('link', { name: 'Go to the vault' })).toHaveAttribute('href', '/vault')
    })

    it('shows progress and a link while a run is working', async () => {
      const region = await briefingFor([{ ...base, status: 'running', counts }])
      await waitFor(() => expect(region).toHaveTextContent('I am analysing your documents now (40% done)'))
      expect(screen.getByRole('link', { name: 'Watch progress' })).toHaveAttribute('href', '/runs/r1')
    })

    it('says a failed run lost nothing', async () => {
      const region = await briefingFor([{ ...base, status: 'failed', counts }])
      await waitFor(() => expect(region).toHaveTextContent('My last analysis did not finish. Nothing is lost'))
    })

    it('reports the result of a finished run', async () => {
      const region = await briefingFor([{ ...base, status: 'done', percent: 100, counts }])
      await waitFor(() => expect(region).toHaveTextContent('I checked 120 papers against your 2 documents and found 2 opportunities'))
    })
  })
})
