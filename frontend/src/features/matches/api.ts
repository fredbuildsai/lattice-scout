import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { track } from '../../analytics/client'
import { apiFetch, apiJson, ApiError, type components } from '../../api/client'

export type AnonymousMatch = components['schemas']['AnonymousMatchView']
export type RevealedMatch = components['schemas']['RevealedMatchView']
export type MatchCard = AnonymousMatch | RevealedMatch
export type MatchList = components['schemas']['MatchListOut']
export type MatchFeedback = components['schemas']['FeedbackOut']
export type DecisionAction = components['schemas']['DecisionIn']['action']
export type MatchReason = NonNullable<components['schemas']['MatchRatingIn']['reasons']>[number]

/** Reason chips for rating a match, in display order. */
export const MATCH_REASONS: { id: MatchReason; label: string }[] = [
  { id: 'methods_fit', label: 'Methods fit' },
  { id: 'right_seniority', label: 'Right seniority' },
  { id: 'shared_timeline', label: 'Shared timeline' },
  { id: 'too_similar', label: 'Too similar to me' },
  { id: 'already_knew_them', label: 'Already knew them' },
]

/** Link to an ORCID record. */
export const orcidUrl = (orcid: string) => `https://orcid.org/${orcid.replace(/^https?:\/\/orcid\.org\//, '')}`
/** Link to an OpenAlex entity (author or work). */
export const openAlexEntityUrl = (id: string) => `https://openalex.org/${id.replace(/^https?:\/\/openalex\.org\//, '')}`
/** Score 0-1 as a whole percentage. */
export const scorePercent = (score: number) => Math.round(score * 100)

/**
 * GET /matches, following `next_cursor` page by page. `data` is flattened to
 * `{ matching_enabled, items, next_cursor }` (all pages loaded so far); call `fetchNextPage` for "Load more".
 */
export function useMatches(limit = 50) {
  return useInfiniteQuery({
    queryKey: ['matches', { limit }],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({ limit: String(limit) })
      if (pageParam) qs.set('cursor', pageParam)
      return apiJson<MatchList>(`/api/v1/matches?${qs}`)
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // The reveal job writes the joint ideas a little after the match turns mutual: look again while any are pending.
    refetchInterval: (q) =>
      q.state.data?.pages.some((p) => p.items.some((m) => m.kind === 'revealed' && (m.ideas_status === 'pending' || m.ideas_status === 'none')))
        ? 4000
        : false,
    select: (d): MatchList => ({
      matching_enabled: d.pages[0]?.matching_enabled ?? false,
      items: d.pages.flatMap((p) => p.items),
      next_cursor: d.pages[d.pages.length - 1]?.next_cursor ?? null,
    }),
  })
}

/** `mailto:` link for the revealed contact: fixed subject, optional body (the user-editable template draft). Opens the user's own mail app; nothing is sent by Scout. */
export const mailtoUrl = (email: string, subject: string, body?: string) =>
  `mailto:${email}?subject=${encodeURIComponent(subject)}${body ? `&body=${encodeURIComponent(body)}` : ''}`

/** POST /matches/{id}/decision; tracks match_decision and match_revealed on a mutual result, then refreshes the list. */
export function useDecision() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      id,
      action,
      neverSimilar = false,
    }: {
      id: string
      action: DecisionAction
      neverSimilar?: boolean
    }) =>
      apiJson<MatchCard>(`/api/v1/matches/${id}/decision`, {
        method: 'POST',
        body: JSON.stringify({ action, never_similar: neverSimilar }),
      }),
    onSuccess: (card, { id, action }) => {
      track('match_decision', { match_id: id, decision: action })
      if (card.status === 'mutual' && action === 'accept') track('match_revealed', { match_id: id })
      return qc.invalidateQueries({ queryKey: ['matches'] })
    },
  })
}

/** GET /feedback for a match; `null` when not rated yet (404). */
export function useMatchFeedback(matchId: string, enabled = true) {
  return useQuery({
    queryKey: ['feedback', 'match', matchId],
    enabled,
    queryFn: async (): Promise<MatchFeedback | null> => {
      const res = await apiFetch(`/api/v1/feedback?target_type=match&target_id=${encodeURIComponent(matchId)}`)
      if (res.status === 404) return null
      if (!res.ok) throw new ApiError(res.status, '/api/v1/feedback')
      return (await res.json()) as MatchFeedback
    },
  })
}

/** POST /matches/{id}/rating; tracks match_rated. */
export function useRateMatch(matchId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { stars: number; reasons: MatchReason[]; comment: string }) =>
      apiJson<MatchFeedback>(`/api/v1/matches/${matchId}/rating`, {
        method: 'POST',
        body: JSON.stringify({
          stars: v.stars,
          reasons: v.reasons.slice(0, 5),
          ...(v.comment.trim() ? { comment: v.comment.trim().slice(0, 1000) } : {}),
        }),
      }),
    onSuccess: (fb, v) => {
      track('match_rated', { match_id: matchId, rating: v.stars })
      qc.setQueryData(['feedback', 'match', matchId], fb)
    },
  })
}

/** Plain-language text for a failed call; 409 problems carry their own `detail`. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return 'This match is no longer available.'
    if (e.detail) return e.detail
  }
  return 'Something went wrong. Try again.'
}
