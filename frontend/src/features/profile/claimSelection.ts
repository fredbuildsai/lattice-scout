import type { CandidateGroup, ClaimBody } from './api'

/** Initially ticked records: the merged ones. `possible` records start unticked. */
export function initialSelection(group: CandidateGroup): string[] {
  return group.records.map((r) => r.author_id)
}

/** Toggle one record; the primary record cannot be removed. */
export function toggleRecord(group: CandidateGroup, selected: string[], authorId: string): string[] {
  if (authorId === group.primary.author_id) return selected
  return selected.includes(authorId) ? selected.filter((id) => id !== authorId) : [...selected, authorId]
}

/** Request body for POST /profile/claim: primary first, no duplicates, only ids of this group. */
export function buildClaim(group: CandidateGroup, selected: string[]): ClaimBody {
  const known = new Set([...group.records, ...group.possible].map((r) => r.author_id))
  const primary = group.primary.author_id
  const rest = selected.filter((id) => id !== primary && known.has(id))
  return { author_ids: [primary, ...new Set(rest)], primary_author_id: primary }
}
