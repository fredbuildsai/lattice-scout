import { describe, expect, it } from 'vitest'
import type { CandidateGroup } from './api'
import { buildClaim, initialSelection, toggleRecord } from './claimSelection'

const rec = (id: string, works: number) => ({ author_id: id, basis: 'orcid', display_name: 'J Foster', institutions: [], orcid: null, works_count: works })
const group = {
  group_id: 'A1',
  primary: rec('A1', 159),
  records: [rec('A1', 159), rec('A2', 3)],
  possible: [rec('A3', 2)],
  includes_records: 2,
  works_count: 162,
  cited_by_count: 1,
  institutions: [],
  top_topics: [],
} as unknown as CandidateGroup

describe('claim selection', () => {
  it('ticks merged records and leaves possible ones unticked', () => {
    expect(initialSelection(group)).toEqual(['A1', 'A2'])
  })
  it('toggles records but never the primary', () => {
    expect(toggleRecord(group, ['A1', 'A2'], 'A1')).toEqual(['A1', 'A2'])
    expect(toggleRecord(group, ['A1', 'A2'], 'A2')).toEqual(['A1'])
    expect(toggleRecord(group, ['A1'], 'A3')).toEqual(['A1', 'A3'])
  })
  it('builds a claim with the primary first and unknown ids dropped', () => {
    expect(buildClaim(group, ['A3', 'A2', 'A2', 'ZZ'])).toEqual({ author_ids: ['A1', 'A3', 'A2'], primary_author_id: 'A1' })
    expect(buildClaim(group, [])).toEqual({ author_ids: ['A1'], primary_author_id: 'A1' })
  })
})
