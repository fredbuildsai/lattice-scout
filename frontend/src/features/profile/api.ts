import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiJson, type components } from '../../api/client'

export type Me = components['schemas']['MeOut']
export type Profile = components['schemas']['ProfileOut']
export type CandidateGroup = components['schemas']['CandidateGroupOut']
export type CandidateRecord = components['schemas']['RecordOut']
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
