import type { CandidateGroup, ClaimBody } from './api'

/** The backend accepts at most this many OpenAlex author ids in one claim. */
export const MAX_CLAIM_RECORDS = 10

/** Initially ticked records: the merged ones. `possible` records start unticked. */
export function initialSelection(group: CandidateGroup): string[] {
  return group.records.map((r) => r.author_id)
}

/** Toggle one record; the primary record (`primaryId`) cannot be removed. */
export function toggleRecord(primaryId: string, selected: string[], authorId: string): string[] {
  if (authorId === primaryId) return selected
  return selected.includes(authorId) ? selected.filter((id) => id !== authorId) : [...selected, authorId]
}

/** Whether any record of `group` is selected (the card counts as "also me"). */
export function groupIncluded(group: CandidateGroup, selected: string[]): boolean {
  return [...group.records, ...group.possible].some((r) => selected.includes(r.author_id))
}

/**
 * Add or remove a further candidate card ("Also me"): adding ticks its merged records, removing drops all of its
 * records. The primary record is never removed.
 */
export function toggleGroup(primaryId: string, group: CandidateGroup, selected: string[]): string[] {
  if (groupIncluded(group, selected)) {
    const ids = new Set([...group.records, ...group.possible].map((r) => r.author_id))
    return selected.filter((id) => id === primaryId || !ids.has(id))
  }
  return [...selected, ...initialSelection(group).filter((id) => !selected.includes(id))]
}

/** Request body for POST /profile/claim: primary first, no duplicates, only ids of the listed groups. */
export function buildClaim(groups: CandidateGroup[], primary: CandidateGroup, selected: string[]): ClaimBody {
  const known = new Set(groups.flatMap((g) => [...g.records, ...g.possible]).map((r) => r.author_id))
  const primaryId = primary.primary.author_id
  const rest = selected.filter((id) => id !== primaryId && known.has(id))
  return { author_ids: [primaryId, ...new Set(rest)], primary_author_id: primaryId }
}
