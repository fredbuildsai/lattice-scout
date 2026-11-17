import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExternalSuggestions } from './ExternalSuggestions'
import type { ExternalSuggestion } from './api'

const ext = (over: Partial<ExternalSuggestion> = {}): ExternalSuggestion => ({
  id: 'e1',
  openalex_id: 'A123',
  openalex_url: 'https://openalex.org/A123',
  name: 'Ada Outsider',
  institution: 'Example University',
  layer: 'in_network',
  vias: ['Bea Bridge', 'Ben Bridge'],
  shared_topics: ['Lignin valorization', 'Bio-based foams'],
  score: 0.5,
  topic_overlap: 0.5,
  formula: 'ext1',
  h_index: 12,
  citations: 3456,
  works_count: 40,
  recent_works: 7,
  state: 'active',
  created_at: '2026-11-17T10:00:00Z',
  ...over,
})

interface Opts {
  items?: ExternalSuggestion[]
  refreshed?: ExternalSuggestion[]
  refreshStatus?: number
  refreshBody?: unknown
}
function setup({ items = [], refreshed = [], refreshStatus = 200, refreshBody }: Opts = {}) {
  const posts: unknown[] = []
  const patches: { url: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const json = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } })
      if (init?.method === 'POST' && url.includes('/api/v1/matches/external/refresh')) {
        posts.push(JSON.parse(String(init.body)))
        return json(refreshBody ?? { items: refreshed }, refreshStatus)
      }
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as { state: ExternalSuggestion['state'] }
        patches.push({ url, body })
        return json(ext({ state: body.state }))
      }
      if (url.includes('/api/v1/matches/external')) return json({ items })
      return new Response('{}', { status: 404 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <ExternalSuggestions />
    </QueryClientProvider>,
  )
  return { posts, patches }
}
afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ExternalSuggestions', () => {
  it('shows the empty state and both scope chips on by default', async () => {
    setup()
    expect(await screen.findByText(/No suggestions yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'In your network' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'In your field' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('searches with the selected scopes and renders the cards', async () => {
    const { posts } = setup({
      refreshed: [ext(), ext({ id: 'e2', name: 'Fay Field', layer: 'in_field', vias: [], openalex_id: 'A9' })],
    })
    const user = userEvent.setup()
    await screen.findByText(/No suggestions yet/)
    await user.click(screen.getByRole('button', { name: 'In your network' }))
    expect(screen.getByRole('button', { name: 'In your network' })).toHaveAttribute('aria-pressed', 'false')
    await user.click(screen.getByRole('button', { name: 'Find researchers' }))
    expect(posts).toEqual([{ scopes: ['in_field'] }])
    const card = (await screen.findByText('Ada Outsider')).closest('li')!
    expect(within(card).getByRole('link', { name: 'Ada Outsider' })).toHaveAttribute(
      'href',
      'https://openalex.org/A123',
    )
    expect(within(card).getByText('via Bea Bridge, Ben Bridge')).toBeInTheDocument()
    expect(within(card).getByText(/Lignin valorization · Bio-based foams/)).toBeInTheDocument()
    expect(within(card).getByText(/h-index 12 · 3,456 citations · 7 works in the last 3 years/)).toBeInTheDocument()
    expect(within(card).getByText('Example University')).toBeInTheDocument()
    const field = screen.getByText('Fay Field').closest('li')!
    expect(within(field).getByText('In your field')).toBeInTheDocument()
    expect(within(field).queryByText(/^via /)).not.toBeInTheDocument()
  })

  it('disables the search when no scope is selected', async () => {
    setup()
    const user = userEvent.setup()
    await screen.findByText(/No suggestions yet/)
    await user.click(screen.getByRole('button', { name: 'In your network' }))
    await user.click(screen.getByRole('button', { name: 'In your field' }))
    expect(screen.getByRole('button', { name: 'Find researchers' })).toBeDisabled()
  })

  it('saves and dismisses a suggestion', async () => {
    const { patches } = setup({ items: [ext()] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Save' }))
    await user.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(patches).toEqual([
      { url: expect.stringContaining('/api/v1/matches/external/e1'), body: { state: 'saved' } },
      { url: expect.stringContaining('/api/v1/matches/external/e1'), body: { state: 'dismissed' } },
    ])
  })

  it('offers Unsave on a saved suggestion', async () => {
    const { patches } = setup({ items: [ext({ state: 'saved' })] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Unsave' }))
    expect(patches[0]?.body).toEqual({ state: 'active' })
  })

  it('explains the daily limit and the missing profile', async () => {
    setup({ refreshStatus: 429, refreshBody: { code: 'external_refresh_limit', detail: 'x' } })
    const user = userEvent.setup()
    await screen.findByText(/No suggestions yet/)
    await user.click(screen.getByRole('button', { name: 'Find researchers' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('today’s search limit')
  })

  it('asks to claim the profile first', async () => {
    setup({ refreshStatus: 409, refreshBody: { code: 'profile_required', detail: 'Claim your profile first' } })
    const user = userEvent.setup()
    await screen.findByText(/No suggestions yet/)
    await user.click(screen.getByRole('button', { name: 'Find researchers' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Claim your OpenAlex profile first')
  })
})
