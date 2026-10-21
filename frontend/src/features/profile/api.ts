import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { analytics } from '../../analytics/client'
import { apiJson, type components } from '../../api/client'

export type Me = components['schemas']['MeOut']
export type Profile = components['schemas']['ProfileOut']
export type CandidateGroup = components['schemas']['CandidateGroupOut']
export type CandidateRecord = components['schemas']['RecordOut']
export type Work = components['schemas']['WorkOut']
export type Topic = components['schemas']['TopicOut']
export type ConsentPatch = components['schemas']['ConsentIn']
export type NotificationPrefs = components['schemas']['NotificationPrefsOut']
export type NotificationPrefsPatch = components['schemas']['NotificationPrefsIn']
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
    onError: (_e, { id, included }) => {
      // Undo only the failed toggle; a second, successful toggle must survive.
      qc.setQueryData<Profile>(['profile'], (p) => (p ? applyIncluded(p, id, !included) : p))
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

/** POST /me/delete: starts account deletion; the caller must retype the account e-mail. Throws `ApiError` (422 `confirmation_mismatch`). */
export function useDeleteAccount() {
  return useMutation({
    mutationFn: (confirmEmail: string) => apiJson<{ deleting?: boolean }>('/api/v1/me/delete', { method: 'POST', body: JSON.stringify({ confirm_email: confirmEmail }) }),
  })
}

/** GET /me/notifications (query key `['notification-prefs']`). */
export function useNotificationPrefs(enabled = true) {
  const q = useQuery({ queryKey: ['notification-prefs'], queryFn: () => apiJson<NotificationPrefs>('/api/v1/me/notifications'), enabled })
  const optOut = q.data?.analytics_opt_out
  // Keep the analytics client in step with the stored preference (also after optimistic updates and rollbacks).
  useEffect(() => {
    if (optOut !== undefined) analytics.setOptOut(optOut)
  }, [optOut])
  return q
}

/** PATCH /me/notifications with a partial body: applies it to the cache at once, rolls back only that change on error. */
export function useUpdateNotificationPrefs() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (patch: NotificationPrefsPatch) =>
      apiJson<NotificationPrefs>('/api/v1/me/notifications', { method: 'PATCH', body: JSON.stringify(patch) }),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: ['notification-prefs'] })
      const previous = qc.getQueryData<NotificationPrefs>(['notification-prefs'])
      if (previous) qc.setQueryData(['notification-prefs'], { ...previous, ...patch })
      return { previous }
    },
    onError: (_e, patch, ctx) => {
      const undo = Object.fromEntries(Object.keys(patch).map((k) => [k, ctx?.previous?.[k as keyof NotificationPrefs]]))
      qc.setQueryData<NotificationPrefs>(['notification-prefs'], (p) => (p ? { ...p, ...undo } : p))
    },
  })
}
