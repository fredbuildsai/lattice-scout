import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../../App'
import { fakeAuthClient, ME_WITH_RESEARCHER, PROFILE } from '../../test-utils'
import { WorksStep } from './WorksStep'
import { applyIncluded, storedClaim, useProfile, type Profile } from './api'

function Live() {
  const q = useProfile()
  return q.data ? <WorksStep profile={q.data} /> : null
}

const work = (i: number, over: Record<string, unknown> = {}) => ({
  id: `w${i}`, openalex_work_id: `W${i}`, title: `Work ${i}`, year: 2020, type: 'article', cited_by: 0,
  included: true, included_by_user: false, uncertain_reason: null, ...over,
})
const profile = (works = [work(1), work(2, { included: false, type: 'dataset', uncertain_reason: 'non_article_type' }), work(3, { included: false, uncertain_reason: 'name_mismatch', cited_by: 12 })]): Profile =>
  ({ ...PROFILE, works, works_summary: { included: works.filter((w) => w.included).length, total: works.length, uncertain: 2 } }) as unknown as Profile

function renderStep(p: Profile) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } })
  qc.setQueryData(['profile'], p)
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Live />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return qc
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(profile()), { status: 200 })))
})

describe('WorksStep', () => {
  it('leaves uncertain works unticked with a reason badge and renders citations only when > 0', () => {
    renderStep(profile())
    expect(screen.getByLabelText(/Work 1/)).toBeChecked()
    expect(screen.getByLabelText(/Work 2/)).not.toBeChecked()
    expect(screen.getByLabelText(/Work 3/)).not.toBeChecked()
    expect(screen.getByText('Not an article')).toBeInTheDocument()
    expect(screen.getByText('Author name differs')).toBeInTheDocument()
    expect(screen.getByText(/12 citations/)).toBeInTheDocument()
    expect(screen.queryByText(/\b0 citations/)).toBeNull()
    expect(screen.getByLabelText('Works summary')).toHaveTextContent('1 of 3 included')
    expect(screen.getByRole('link', { name: 'Continue to Home' })).toHaveAttribute('href', '/')
  })

  it('shows the first 20 works and all on request', async () => {
    renderStep(profile(Array.from({ length: 25 }, (_, i) => work(i + 1))))
    expect(screen.getAllByRole('checkbox')).toHaveLength(20)
    await userEvent.click(screen.getByRole('button', { name: 'Show all 25' }))
    expect(screen.getAllByRole('checkbox')).toHaveLength(25)
  })

  it('updates optimistically, PATCHes the work and refetches the profile', async () => {
    const calls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${url}`)
      return new Response(JSON.stringify(init?.method === 'PATCH' ? work(2, { included: true }) : applyIncluded(profile(), 'w2', true)), { status: 200 })
    }))
    renderStep(profile())
    await userEvent.click(screen.getByLabelText(/Work 2/))
    expect(screen.getByLabelText(/Work 2/)).toBeChecked()
    expect(screen.getByLabelText('Works summary')).toHaveTextContent('2 of 3 included')
    await waitFor(() => expect(calls.some((c) => c.startsWith('PATCH') && c.endsWith('/profile/works/w2'))).toBe(true))
    await waitFor(() => expect(calls.some((c) => c.startsWith('GET') && c.endsWith('/api/v1/profile'))).toBe(true))
  })

  it('rolls back and shows an error when the PATCH fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init?: RequestInit) => (init?.method === 'PATCH' ? new Response('{}', { status: 500 }) : new Response(JSON.stringify(profile()), { status: 200 }))))
    renderStep(profile())
    await userEvent.click(screen.getByLabelText(/Work 2/))
    expect(await screen.findByRole('alert')).toHaveTextContent(/reverted/)
    await waitFor(() => expect(screen.getByLabelText(/Work 2/)).not.toBeChecked())
    expect(screen.getByLabelText('Works summary')).toHaveTextContent('1 of 3 included')
  })
})

describe('helpers', () => {
  it('applyIncluded recounts the summary', () => {
    expect(applyIncluded(profile(), 'w2', true).works_summary.included).toBe(2)
  })
  it('storedClaim puts the primary record first', () => {
    const p = { ...profile(), openalex_ids: [{ author_id: 'A2', merged: true, basis: 'orcid', works_count: 1 }, { author_id: 'A1', merged: false, basis: 'primary', works_count: 5 }] } as Profile
    expect(storedClaim(p)).toEqual({ author_ids: ['A1', 'A2'], primary_author_id: 'A1' })
  })
})

describe('failed import', () => {
  it('Retry import re-POSTs the stored claim and Search again shows the claim form', async () => {
    const failed = { ...PROFILE, import: { status: 'failed', generation: 1 }, openalex_ids: [{ author_id: 'A2', merged: true, basis: 'orcid', works_count: 1 }, { author_id: 'A1', merged: false, basis: 'primary', works_count: 5 }] }
    const posts: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') {
        posts.push(`${url} ${init.body}`)
        return new Response(JSON.stringify({ ...failed, import: { status: 'queued', generation: 2 } }), { status: 200 })
      }
      const body = String(url).includes('/api/v1/profile') ? failed : ME_WITH_RESEARCHER
      return new Response(JSON.stringify(body), { status: 200 })
    }))
    render(
      <MemoryRouter initialEntries={['/onboarding?step=works']}>
        <App authClient={fakeAuthClient()} client={new QueryClient()} />
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Retry import' }))
    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]!).toContain('/api/v1/profile/claim')
    expect(JSON.parse(posts[0]!.split(' ').slice(1).join(' '))).toEqual({ author_ids: ['A1', 'A2'], primary_author_id: 'A1' })
  })
})
