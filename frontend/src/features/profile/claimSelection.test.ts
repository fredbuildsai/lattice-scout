import { describe, expect, it } from 'vitest'
import type { CandidateGroup } from './api'
import { buildClaim, groupIncluded, initialSelection, toggleGroup, toggleRecord } from './claimSelection'

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
    expect(toggleRecord('A1', ['A1', 'A2'], 'A1')).toEqual(['A1', 'A2'])
    expect(toggleRecord('A1', ['A1', 'A2'], 'A2')).toEqual(['A1'])
    expect(toggleRecord('A1', ['A1'], 'A3')).toEqual(['A1', 'A3'])
  })
  it('builds a claim with the primary first and unknown ids dropped', () => {
    expect(buildClaim([group], group, ['A3', 'A2', 'A2', 'ZZ'])).toEqual({ author_ids: ['A1', 'A3', 'A2'], primary_author_id: 'A1' })
    expect(buildClaim([group], group, [])).toEqual({ author_ids: ['A1'], primary_author_id: 'A1' })
  })
  it('adds and removes a second card of the same person, keeping the primary', () => {
    const other = { ...group, group_id: 'B1', primary: rec('B1', 4), records: [rec('B1', 4), rec('B2', 1)], possible: [rec('B3', 1)] } as unknown as CandidateGroup
    const withOther = toggleGroup('A1', other, ['A1', 'A2'])
    expect(withOther).toEqual(['A1', 'A2', 'B1', 'B2'])
    expect(groupIncluded(other, withOther)).toBe(true)
    expect(buildClaim([group, other], group, withOther)).toEqual({ author_ids: ['A1', 'A2', 'B1', 'B2'], primary_author_id: 'A1' })
    expect(toggleGroup('A1', other, [...withOther, 'B3'])).toEqual(['A1', 'A2'])
    expect(groupIncluded(other, ['A1', 'A2'])).toBe(false)
  })
})
