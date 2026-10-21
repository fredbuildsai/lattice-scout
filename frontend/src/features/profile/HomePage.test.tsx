import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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
})
