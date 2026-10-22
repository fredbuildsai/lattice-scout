import type { ClaimData, Evidence, OpportunityCard } from './api'

export const lit: Evidence = { id: 'e1', source_type: 'literature', title: 'Lignin foams', authors: ['A. One', 'B. Two'], year: 2025, doi: '10.1/abc', work_id: 'W123', quote: 'Foams meet spec.', original_available: false }
export const vault: Evidence = { id: 'e2', source_type: 'vault', filename: 'proposal.pdf', page_from: 3, page_to: 4, quote: 'Not shown that foams pass.', original_available: false, authors: [] }
export const count: Evidence = { id: 'e3', source_type: 'count', query: 'lignin AND foam', count: 141, since_year: 2021, original_available: false, authors: [] }

export const claim = (over: Partial<ClaimData> = {}): ClaimData => ({ id: 'c1', text: 'Foams are promising.', markers: [lit, vault, count], ...over })

export const card = (id: string, kind: OpportunityCard['kind'] = 'white_spot', over: Partial<OpportunityCard> = {}): OpportunityCard => ({
  id, run_id: 'r1', kind, title: `Idea ${id}`, why: claim({ id: `c-${id}` }), scores: { fit: 0.8, novelty: 0.7, readiness: 0.6, effort: 0.3 },
  novelty: { count: 3, since_year: 2021, query: 'q', band: null }, status: 'shown', verified_sources: 4, clusters: [], basis: 'full', created_at: '2026-10-20T00:00:00Z', ...over,
})
