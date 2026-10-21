import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as analytics from '../../analytics/client'
import { ME_WITH_RESEARCHER } from '../../test-utils'
import { anon, revealed } from './fixtures'
import { MatchesPage } from './MatchesPage'
import type { MatchCard } from './api'

interface Opts {
  enabled?: boolean
  items?: MatchCard[]
  feedback?: unknown
  decisionResponse?: { status: number; body: unknown }
  moreItems?: MatchCard[]
}
function setup({ enabled = true, items = [], feedback = null, decisionResponse, moreItems = [] }: Opts = {}) {
  const matchUrls: string[] = []
  const posts: { url: string; body: Record<string, unknown> }[] = []
  const patches: unknown[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const json = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), {
          status,
          headers: { 'Content-Type': 'application/json' },
        })
      if (init?.method === 'PATCH') {
        patches.push(JSON.parse(String(init.body)))
        return json({ ...ME_WITH_RESEARCHER, consent_matching: true })
      }
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body)) })
        if (url.includes('/rating'))
          return json({
            target_type: 'match',
            target_id: 'm2',
            rating: null,
            stars: 4,
            reasons: [],
            comment: null,
            updated_at: 'x',
          })
        return decisionResponse
          ? json(decisionResponse.body, decisionResponse.status)
          : json(anon({ status: 'waiting_on_them' }))
      }
      if (url.includes('/api/v1/matches')) {
        matchUrls.push(url)
        if (url.includes('cursor=c1'))
          return json({
            matching_enabled: enabled,
            items: moreItems,
            next_cursor: null,
          })
        return json({
          matching_enabled: enabled,
          items,
          next_cursor: moreItems.length > 0 ? 'c1' : null,
        })
      }
      if (url.includes('/api/v1/feedback')) return feedback ? json(feedback) : new Response('{}', { status: 404 })
      if (url.includes('/api/v1/me')) return json(ME_WITH_RESEARCHER)
      return new Response('{}', { status: 404 })
    }),
  )
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <MatchesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { posts, patches, matchUrls }
}
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('MatchesPage', () => {
  it('shows an awaiting card with percentage and accepts', async () => {
    const track = vi.spyOn(analytics, 'track').mockImplementation(() => undefined)
    const { posts } = setup({ items: [anon()] })
    const user = userEvent.setup()
    expect(await screen.findByRole('article', { name: 'Anonymous match 88%' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Anonymous match' })).toBeInTheDocument()
    expect(screen.getByText('Europe')).toBeInTheDocument()
    expect(screen.getByText('Iron-oxide catalysts')).toBeInTheDocument()
    expect(track).toHaveBeenCalledWith('match_viewed', { match_id: 'm1' })
    await user.click(screen.getByRole('button', { name: 'Accept' }))
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: 'accept',
        never_similar: false,
      }),
    )
    expect(track).toHaveBeenCalledWith('match_decision', {
      match_id: 'm1',
      decision: 'accept',
    })
  })

  it('confirms a decline and sends never_similar', async () => {
    const { posts } = setup({ items: [anon()] })
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Decline' }))
    expect(posts).toHaveLength(0)
    await user.click(screen.getByLabelText('Never suggest this person again'))
    await user.click(screen.getByRole('button', { name: 'Confirm decline' }))
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: 'decline',
        never_similar: true,
      }),
    )
  })

  it('shows the waiting state with a withdraw button', async () => {
    const { posts } = setup({
      items: [anon({ status: 'waiting_on_them', my_decision: 'accept' })],
    })
    const user = userEvent.setup()
    expect(await screen.findByText(/Waiting for the other researcher/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Withdraw' }))
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        action: 'withdraw',
        never_similar: false,
      }),
    )
  })

  it('uses neutral wording for declined and expired cards', async () => {
    setup({
      items: [
        anon({ id: 'a', status: 'declined_by_you', my_decision: 'decline' }),
        anon({ id: 'b', status: 'expired' }),
      ],
    })
    await screen.findByText(/You passed on this proposal/)
    const text = () => document.body.textContent ?? ''
    await userEvent.setup().click(screen.getByRole('button', { name: /Expired/ }))
    expect(screen.getByText(/expired on/)).toBeInTheDocument()
    expect(text()).not.toMatch(/they declined|declined you|rejected you|turned you down/i)
    expect(screen.queryByRole('button', { name: 'Accept' })).toBeNull()
  })

  it('shows the server detail when a decision conflicts', async () => {
    setup({
      items: [anon()],
      decisionResponse: {
        status: 409,
        body: { detail: 'This proposal has expired.' },
      },
    })
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Accept' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This proposal has expired.')
  })

  it('renders a revealed card with contact links, ideas and rating prefill', async () => {
    const { posts } = setup({
      items: [revealed()],
      feedback: {
        target_type: 'match',
        target_id: 'm2',
        rating: null,
        stars: 3,
        reasons: ['methods_fit'],
        comment: 'nice',
        updated_at: 'x',
      },
    })
    const user = userEvent.setup()
    expect(await screen.findByRole('heading', { name: 'Ada Example' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'ada@example.edu' })).toHaveAttribute('href', 'mailto:ada@example.edu')
    expect(screen.getByRole('link', { name: /ORCID 0000/ })).toHaveAttribute(
      'href',
      'https://orcid.org/0000-0001-6636-725X',
    )
    expect(screen.getByRole('link', { name: /OpenAlex A123/ })).toHaveAttribute('href', 'https://openalex.org/A123')
    const ideas = screen.getByRole('list', { name: 'Joint opportunities' })
    expect(within(ideas).getByText('Capture and destroy')).toBeInTheDocument()
    expect(within(ideas).getByText('Can it regenerate?')).toBeInTheDocument()
    await vi.waitFor(() =>
      expect(screen.getByRole('button', { name: 'Methods fit' })).toHaveAttribute('aria-pressed', 'true'),
    )
    expect(screen.getByLabelText('Anything else?')).toHaveValue('nice')
    expect(screen.getByRole('button', { name: '3 stars' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(screen.getByRole('button', { name: '4 stars' }))
    await user.click(screen.getByRole('button', { name: 'Shared timeline' }))
    await user.click(screen.getByRole('button', { name: 'Send feedback' }))
    await vi.waitFor(() =>
      expect(posts[0]?.body).toEqual({
        stars: 4,
        reasons: ['methods_fit', 'shared_timeline'],
        comment: 'nice',
      }),
    )
    expect(posts[0]?.url).toContain('/matches/m2/rating')
  })

  it('shows pending and failed idea states', async () => {
    setup({
      items: [
        revealed({ id: 'p', ideas_status: 'pending', joint_ideas: [] }),
        revealed({ id: 'f', ideas_status: 'failed', joint_ideas: [] }),
      ],
    })
    expect(await screen.findByText(/Ideas are being prepared/)).toBeInTheDocument()
    const rows = screen.getAllByRole('button', { name: /Mutual match/ })
    await userEvent.setup().click(rows[1]!)
    expect(screen.getByText(/could not write joint ideas/)).toBeInTheDocument()
  })

  it('shows the You both said yes hero and real mailto buttons only', async () => {
    setup({ items: [revealed()] })
    expect(await screen.findByText('You both said yes')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Write an intro' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^mailto:ada@example\.edu\?subject=Lattice%20Scout%3A%20introduction&body=Dear%20Ada%20Example/),
    )
    expect(screen.getByRole('link', { name: 'Propose call' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^mailto:ada@example\.edu\?subject=/),
    )
    expect(screen.queryByText(/workspace/i)).toBeNull()
  })

  it('follows next_cursor with Load more and keeps the selection', async () => {
    const { matchUrls } = setup({
      items: [anon({ id: 'a' })],
      moreItems: [anon({ id: 'b', status: 'waiting_on_them' })],
    })
    const user = userEvent.setup()
    await screen.findByRole('button', { name: /Anonymous match/ })
    expect(screen.queryByText(/Waiting on them · 1/)).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Load more' }))
    expect(await screen.findByText(/Waiting on them · 1/)).toBeInTheDocument()
    expect(matchUrls.some((u) => u.includes('cursor=c1'))).toBe(true)
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
    expect(screen.getByRole('button', { name: /Waiting on you/ })).toHaveAttribute('aria-current', 'true')
  })

  it('says truthfully what the anonymous card shows', async () => {
    setup({ enabled: false })
    expect(await screen.findByText(/topics you share and short descriptions of the methods/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/they see only your field/)
  })

  it('offers the opt-in when matching is off and PATCHes consent', async () => {
    const { patches } = setup({ enabled: false })
    await userEvent.setup().click(
      await screen.findByRole('button', {
        name: /Suggest me to complementary/,
      }),
    )
    await vi.waitFor(() => expect(patches).toEqual([{ consent_matching: true }]))
  })

  it('shows the invite message when enabled but empty', async () => {
    setup({ items: [] })
    expect(await screen.findByText(/Matching opens when more researchers in your field join/)).toBeInTheDocument()
  })

  it('shows career start and joint papers on a revealed match only when present', async () => {
    setup({ items: [revealed({ career_since: 2013, joint_works_count: 5 })] })
    expect(await screen.findByText('Mid career since 2013')).toBeInTheDocument()
    expect(screen.getByText('5 joint papers')).toBeInTheDocument()
  })

  it('omits the career line and the joint count when the backend sends none', async () => {
    setup({ items: [revealed({ career_since: null, joint_works_count: null })] })
    await screen.findByText('You both said yes')
    expect(screen.queryByText(/since 20/)).toBeNull()
    expect(screen.queryByText(/joint paper/)).toBeNull()
  })

  it('uses the singular for one joint paper', async () => {
    setup({ items: [revealed({ joint_works_count: 1 })] })
    expect(await screen.findByText('1 joint paper')).toBeInTheDocument()
  })

  it('never shows career or joint lines on the anonymous card, even if the payload carried them', async () => {
    const leaky = { ...anon(), career_since: 2013, joint_works_count: 5 } as unknown as MatchCard
    setup({ items: [leaky] })
    await screen.findByRole('article', { name: /Anonymous match/ })
    expect(screen.queryByText(/since 2013/)).toBeNull()
    expect(screen.queryByText(/joint paper/)).toBeNull()
  })

  it('draws the Venn diagram with a text alternative on anonymous and revealed cards', async () => {
    setup({ items: [anon({ shared_topics: ['PFAS', 'Membranes'] })] })
    const venn = await screen.findByRole('img', { name: /Venn diagram/ })
    const label = venn.getAttribute('aria-label') ?? ''
    expect(label).toContain('You bring: Fungal membrane')
    expect(label).toContain('They bring: Iron-oxide catalysts')
    expect(label).toContain('Shared topics: PFAS, Membranes')
  })

  it('states that no topics are shared in the Venn text alternative', async () => {
    setup({ items: [revealed({ shared_topics: [] })] })
    expect(await screen.findByRole('img', { name: /Shared topics: none/ })).toBeInTheDocument()
  })

  it('offers an editable intro draft that feeds the mailto body, with copy and reset', async () => {
    const writeText = vi.fn(async () => undefined)
    setup({ items: [revealed({ shared_topics: ['PFAS'] })] })
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    const user = userEvent.setup()
    const box = await screen.findByRole('textbox', { name: /Intro message/ })
    expect((box as HTMLTextAreaElement).value).toContain('Dear Ada Example,')
    await user.clear(box)
    await user.type(box, 'Hi Ada')
    expect(screen.getByRole('link', { name: 'Open in email app' })).toHaveAttribute(
      'href',
      'mailto:ada@example.edu?subject=Lattice%20Scout%3A%20introduction&body=Hi%20Ada',
    )
    await user.click(screen.getByRole('button', { name: 'Copy' }))
    expect(await screen.findByText('Copied.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reset to template' }))
    expect((box as HTMLTextAreaElement).value).toContain('Dear Ada Example,')
  })
})
