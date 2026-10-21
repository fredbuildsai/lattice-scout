import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as analytics from '../../analytics/client'
import { Claim } from './Claim'
import { SourceMarker } from './SourceMarker'
import { claim, count, lit, vault } from './fixtures'

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>)
}

let track: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  track = vi.spyOn(analytics, 'track').mockImplementation(() => undefined)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('SourceMarker', () => {
  it('shows literature details with DOI and OpenAlex links and tracks both events', async () => {
    const user = userEvent.setup()
    wrap(<SourceMarker evidence={lit} index={1} opportunityId="o1" claimId="c1" />)
    await user.click(screen.getByRole('button', { name: /Source 1: Lignin foams/ }))
    expect(track).toHaveBeenCalledWith('evidence_marker_opened', { opportunity_id: 'o1', claim_id: 'c1' })
    expect(screen.getByRole('dialog')).toHaveTextContent('A. One, B. Two · 2025')
    expect(screen.getByText(/Foams meet spec/)).toBeInTheDocument()
    const doi = screen.getByRole('link', { name: 'Open DOI' })
    expect(doi).toHaveAttribute('href', 'https://doi.org/10.1/abc')
    expect(screen.getByRole('link', { name: 'Open in OpenAlex' })).toHaveAttribute('href', 'https://openalex.org/W123')
    await user.click(doi)
    expect(track).toHaveBeenCalledWith('evidence_source_clicked', { opportunity_id: 'o1', evidence_id: 'e1', source_type: 'literature' })
  })

  it('shows the vault quote and loads the passage with the original-not-kept note', async () => {
    const user = userEvent.setup()
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ kind: 'vault', id: 'e2', passage: 'Full passage text', original_available: false }), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    wrap(<SourceMarker evidence={vault} index={2} opportunityId="o1" claimId="c1" />)
    await user.click(screen.getByRole('button', { name: /Source 2: proposal.pdf/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent('proposal.pdf · pp. 3–4')
    expect(screen.getByText(/Not shown that foams pass/).tagName).toBe('MARK')
    await user.click(screen.getByRole('button', { name: 'Open passage' }))
    expect(await screen.findByText('Full passage text')).toBeInTheDocument()
    expect(screen.getByText('Original file not kept; showing the saved quote')).toBeInTheDocument()
    expect(String(fetchMock.mock.calls[0]![0])).toContain('/api/v1/evidence/e2/open')
    expect(track).toHaveBeenCalledWith('evidence_source_clicked', { opportunity_id: 'o1', evidence_id: 'e2', source_type: 'vault' })
  })

  it('shows query and count for novelty evidence', async () => {
    const user = userEvent.setup()
    wrap(<SourceMarker evidence={count} index={3} opportunityId="o1" claimId="c1" />)
    await user.click(screen.getByRole('button', { name: /Source 3/ }))
    expect(screen.getByRole('dialog')).toHaveTextContent('141 papers since 2021')
    expect(screen.getByText('lignin AND foam')).toBeInTheDocument()
  })

  it('closes on Escape and returns focus to the marker', async () => {
    const user = userEvent.setup()
    wrap(<SourceMarker evidence={lit} index={1} opportunityId="o1" claimId="c1" />)
    const btn = screen.getByRole('button', { name: /Source 1/ })
    await user.click(btn)
    expect(btn).toHaveAttribute('aria-expanded', 'true')
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(btn).toHaveFocus()
  })
})

describe('Claim', () => {
  it('renders the text followed by numbered markers', () => {
    wrap(<Claim claim={claim()} opportunityId="o1" />)
    expect(screen.getByText(/Foams are promising\./)).toBeInTheDocument()
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['1', '2', '3'])
  })
})
