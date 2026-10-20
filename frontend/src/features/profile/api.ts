import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiJson, type components } from '../../api/client'

export type Me = components['schemas']['MeOut']
export type Profile = components['schemas']['ProfileOut']
export type CandidateGroup = components['schemas']['CandidateGroupOut']
export type CandidateRecord = components['schemas']['RecordOut']
export type Work = components['schemas']['WorkOut']
export type Topic = components['schemas']['TopicOut']
export type ConsentPatch = components['schemas']['ConsentIn']
export type ClaimBody = components['schemas']['ClaimIn']

/** The signed-in account (query key `['me']`). Enabled only when signed in. */
export function useMe(enabled = true) {
  return useQuery({ queryKey: ['me'], queryFn: () => apiJson<Me>('/api/v1/me'), enabled })
}

/** The researcher profile; polls every 2 s while the import is queued or running. */
export function useProfile(enabled = true) {
  return useQuery({
    queryKey: ['profile'],
    queryFn: () => apiJson<Profile>('/api/v1/profile'),
    enabled,
    refetchInterval: (q) => {
      const s = q.state.data?.import.status
      return s === 'queued' || s === 'importing' ? 2000 : false
    },
  })
}

/** Candidate search; runs only when a search was submitted. */
export function useCandidates(name: string, affiliation: string, enabled: boolean) {
  return useQuery({
    queryKey: ['candidates', name, affiliation],
    enabled,
    queryFn: () => {
      const p = new URLSearchParams({ name })
      if (affiliation) p.set('affiliation', affiliation)
      return apiJson<{ groups: CandidateGroup[] }>(`/api/v1/profile/candidates?${p}`)
    },
  })
}

/** PATCH /me/consent; updates the cached account. */
export function useConsent() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (patch: ConsentPatch) =>
      apiJson<Me>('/api/v1/me/consent', { method: 'PATCH', body: JSON.stringify(patch) }),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

/** POST /profile/claim. */
export function useClaim() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ClaimBody) =>
      apiJson<Profile>('/api/v1/profile/claim', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: async (profile) => {
      qc.setQueryData(['profile'], profile)
      await qc.invalidateQueries({ queryKey: ['me'] })
    },
  })
}

/** Optimistically apply an include flag to the cached profile (summary included). */
export function applyIncluded(profile: Profile, workId: string, included: boolean): Profile {
  const works = profile.works.map((w) => (w.id === workId ? { ...w, included, included_by_user: true } : w))
  return { ...profile, works, works_summary: { ...profile.works_summary, included: works.filter((w) => w.included).length } }
}

/**
 * PATCH /profile/works/{id}: updates the cached profile at once, rolls back on error and
 * refetches the profile afterwards so the topics (computed from included works) refresh.
 */
export function useToggleWork() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, included }: { id: string; included: boolean }) =>
      apiJson<Work>(`/api/v1/profile/works/${id}`, { method: 'PATCH', body: JSON.stringify({ included }) }),
    onMutate: async ({ id, included }) => {
      await qc.cancelQueries({ queryKey: ['profile'] })
      const previous = qc.getQueryData<Profile>(['profile'])
      if (previous) qc.setQueryData(['profile'], applyIncluded(previous, id, included))
      return { previous }
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(['profile'], ctx.previous)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ['profile'] }),
  })
}

/** The stored claim (primary first) from the profile, for re-posting it. */
export function storedClaim(profile: Profile): ClaimBody {
  const primary = profile.openalex_ids.find((i) => !i.merged) ?? profile.openalex_ids[0]
  const ids = profile.openalex_ids.map((i) => i.author_id)
  const first = primary?.author_id
  return { author_ids: first ? [first, ...ids.filter((i) => i !== first)] : ids, primary_author_id: first ?? null }
}
