import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as analytics from '../../analytics/client'
import { FeedbackPanel } from './FeedbackPanel'

const stored = { target_type: 'opportunity', target_id: 'o1', rating: 'up', reasons: ['spot_on'], comment: null, updated_at: 'x' }

function setup(existing: unknown | null) {
  const posts: unknown[] = []
  vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      posts.push(JSON.parse(String(init.body)))
      const b = posts[posts.length - 1] as Record<string, unknown>
      return new Response(JSON.stringify({ ...stored, ...b }), { status: 201 })
    }
    return existing ? new Response(JSON.stringify(existing), { status: 200 }) : new Response('{}', { status: 404 })
  }))
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <FeedbackPanel opportunityId="o1" />
    </QueryClientProvider>,
  )
  return posts
}
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('FeedbackPanel', () => {
  it('posts the rating with the chosen reasons and tracks feedback_given', async () => {
    const track = vi.spyOn(analytics, 'track').mockImplementation(() => undefined)
    const posts = setup(null)
    const user = userEvent.setup()
    await screen.findByText(/tunes what Scout looks for/)
    await user.click(screen.getByRole('button', { name: 'Already knew' }))
    await user.click(screen.getByRole('button', { name: 'Too vague' }))
    await user.click(screen.getByRole('button', { name: /thumbs down/ }))
    await screen.findByText(/feedback is saved/)
    expect(posts).toEqual([{ target_type: 'opportunity', target_id: 'o1', rating: 'down', reasons: ['already_knew', 'too_vague'] }])
    expect(track).toHaveBeenCalledWith('feedback_given', { target_type: 'opportunity', target_id: 'o1', rating: 'down' })
  })

  it('prefills from an earlier rating and re-saves when a chip changes', async () => {
    const posts = setup(stored)
    const user = userEvent.setup()
    const chip = await screen.findByRole('button', { name: 'Spot on' })
    await vi.waitFor(() => expect(chip).toHaveAttribute('aria-pressed', 'true'))
    expect(screen.getByRole('button', { name: /thumbs up/ })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: 'Wrong field' }))
    await vi.waitFor(() => expect(posts).toEqual([{ target_type: 'opportunity', target_id: 'o1', rating: 'up', reasons: ['spot_on', 'wrong_field'] }]))
  })
})
