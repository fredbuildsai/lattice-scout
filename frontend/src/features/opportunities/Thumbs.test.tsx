import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Thumbs } from './Thumbs'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('Thumbs on a list card', () => {
  it('posts the rating alone, so saved reasons and comment are not erased (M4 review 3)', async () => {
    const posts: Record<string, unknown>[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_u: string, init?: RequestInit) => {
        if (init?.method === 'POST') {
          posts.push(JSON.parse(String(init.body)))
          return new Response(JSON.stringify({ target_type: 'opportunity', target_id: 'o1', rating: 'up', reasons: ['not_feasible'], comment: 'kept', stars: null, updated_at: 'x' }))
        }
        return new Response(JSON.stringify({ target_type: 'opportunity', target_id: 'o1', rating: 'down', reasons: ['not_feasible'], comment: 'kept', stars: null, updated_at: 'x' }))
      }),
    )
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Thumbs opportunityId="o1" />
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('button', { name: /thumbs up/i }))
    await vi.waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]).toEqual({ target_type: 'opportunity', target_id: 'o1', rating: 'up' })
    expect(posts[0]).not.toHaveProperty('reasons')
  })
})
