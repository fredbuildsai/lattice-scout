import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { track } from '../../analytics/client'
import { apiJson, type components } from '../../api/client'

export type Run = components['schemas']['RunOut']

/** True while the worker may still change the run. */
export function isRunActive(status: string | undefined): boolean {
  return status === 'queued' || status === 'running'
}

/** GET /runs/{id}; polls every 2 s while the run is queued or running. */
export function useRun(id: string) {
  return useQuery({
    queryKey: ['runs', id],
    queryFn: () => apiJson<Run>(`/api/v1/runs/${id}`),
    refetchInterval: (q) => (isRunActive(q.state.data?.status) ? 2000 : false),
    refetchIntervalInBackground: false,
  })
}

/** GET /runs: newest first. Polls while the newest run is active. */
export function useRuns() {
  return useQuery({
    queryKey: ['runs'],
    queryFn: () => apiJson<{ items: Run[] }>('/api/v1/runs'),
    refetchInterval: (q) => (isRunActive(q.state.data?.items[0]?.status) ? 4000 : false),
  })
}

/** Latest run that finished (or null). */
export function latestDone(runs: readonly Run[] | undefined): Run | null {
  return runs?.find((r) => r.status === 'done') ?? null
}

/** POST /runs. An empty `documentIds` is never sent; callers choose the documents. */
export function useStartRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (documentIds: string[]) => apiJson<Run>('/api/v1/runs', { method: 'POST', body: JSON.stringify({ document_ids: documentIds }) }),
    onSuccess: (run) => {
      track('run_started', { run_id: run.id, documents: run.document_ids.length })
      qc.setQueryData(['runs', run.id], run)
      return qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

/** POST /runs/{id}/cancel. */
export function useCancelRun(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiJson<Run>(`/api/v1/runs/${id}/cancel`, { method: 'POST' }),
    onSuccess: (run) => {
      qc.setQueryData(['runs', id], run)
      return qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}

export type SystemStatus = components['schemas']['SystemStatusOut']

/** GET /system/status: whether new analyses are accepted (daily budget). Refreshes every minute. */
export function useSystemStatus() {
  return useQuery({ queryKey: ['system-status'], queryFn: () => apiJson<SystemStatus>('/api/v1/system/status'), refetchInterval: 60_000, staleTime: 30_000 })
}
