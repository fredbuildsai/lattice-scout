import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { track } from '../../analytics/client'
import { apiFetch, apiJson, ApiError, type components } from '../../api/client'

export type OpportunityCard = components['schemas']['OpportunityCardOut']
export type OpportunityDetail = components['schemas']['OpportunityDetailOut']
export type ClaimData = components['schemas']['ClaimOut']
export type Evidence = components['schemas']['EvidenceOut']
export type EvidenceOpen = components['schemas']['EvidenceOpenOut']
export type Feedback = components['schemas']['FeedbackOut']
export type UserStatus = 'shown' | 'saved' | 'dismissed'

/** Reasons accepted by POST /feedback, with their chip labels. */
export const FEEDBACK_REASONS: { id: string; label: string }[] = [
  { id: 'spot_on', label: 'Spot on' },
  { id: 'already_knew', label: 'Already knew' },
  { id: 'not_feasible', label: 'Not feasible' },
  { id: 'wrong_field', label: 'Wrong field' },
  { id: 'too_vague', label: 'Too vague' },
]

/** GET /opportunities (up to 50). `runId` limits it to one run; `status` defaults to shown + saved on the server. */
export function useOpportunities(opts: { runId?: string; status?: UserStatus; limit?: number; enabled?: boolean } = {}) {
  const { runId, status, limit = 50, enabled = true } = opts
  const qs = new URLSearchParams({ limit: String(limit) })
  if (runId) qs.set('run_id', runId)
  if (status) qs.set('status', status)
  return useQuery({
    queryKey: ['opportunities', { runId, status, limit }],
    enabled,
    queryFn: () => apiJson<{ items: OpportunityCard[]; next_cursor: string | null }>(`/api/v1/opportunities?${qs}`),
  })
}

/** GET /opportunities/{id}. */
export function useOpportunity(id: string) {
  return useQuery({ queryKey: ['opportunity', id], queryFn: () => apiJson<OpportunityDetail>(`/api/v1/opportunities/${id}`) })
}

/** PATCH /opportunities/{id}; tracks saved / dismissed. */
export function useSetStatus() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status }: { id: string; status: UserStatus }) =>
      apiJson<OpportunityCard>(`/api/v1/opportunities/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) }),
    onSuccess: (_d, { id, status }) => {
      if (status === 'saved') track('opportunity_saved', { opportunity_id: id })
      if (status === 'dismissed') track('opportunity_dismissed', { opportunity_id: id })
      void qc.invalidateQueries({ queryKey: ['opportunity', id] })
      return qc.invalidateQueries({ queryKey: ['opportunities'] })
    },
  })
}

/** GET /evidence/{id}/open, fetched only when `enabled` (user asked to open the passage). */
export function useEvidenceOpen(id: string, enabled: boolean) {
  return useQuery({ queryKey: ['evidence', id], enabled, queryFn: () => apiJson<EvidenceOpen>(`/api/v1/evidence/${id}/open`) })
}

/** GET /feedback for one opportunity; `null` when not rated yet (404). */
export function useFeedback(opportunityId: string) {
  return useQuery({
    queryKey: ['feedback', opportunityId],
    queryFn: async (): Promise<Feedback | null> => {
      const res = await apiFetch(`/api/v1/feedback?target_type=opportunity&target_id=${encodeURIComponent(opportunityId)}`)
      if (res.status === 404) return null
      if (!res.ok) throw new ApiError(res.status, '/api/v1/feedback')
      return (await res.json()) as Feedback
    },
  })
}

/** POST /feedback (a new rating replaces the old one); tracks feedback_given. */
export function useGiveFeedback(opportunityId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: { rating: 'up' | 'down'; reasons?: string[] }) =>
      apiJson<Feedback>('/api/v1/feedback', {
        method: 'POST',
        body: JSON.stringify({ target_type: 'opportunity', target_id: opportunityId, rating: v.rating, ...(v.reasons ? { reasons: v.reasons.slice(0, 5) } : {}) }),
      }),
    onSuccess: (fb, v) => {
      track('feedback_given', { target_type: 'opportunity', target_id: opportunityId, rating: v.rating })
      qc.setQueryData(['feedback', opportunityId], fb)
    },
  })
}

/** Link to the DOI landing page. */
export const doiUrl = (doi: string) => `https://doi.org/${doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, '')}`
/** Link to the OpenAlex work page. */
export const openAlexUrl = (workId: string) => `https://openalex.org/${workId.replace(/^https?:\/\/openalex\.org\//, '')}`

/** Page label such as "p. 3" or "pp. 3–4". */
export function pageLabel(from?: number | null, to?: number | null): string | null {
  if (from == null) return null
  return to == null || to === from ? `p. ${from}` : `pp. ${from}–${to}`
}
