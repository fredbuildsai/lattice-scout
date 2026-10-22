import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { analytics } from '../../analytics/client'
import { apiFetch, type components } from '../../api/client'
import { API_URL } from '../../api/config'
import { getAuthHooks } from '../../api/auth-hooks'
import { hasActiveDocuments, type DocKind } from './status'

export type VaultDocument = components['schemas']['DocumentOut']
export type VaultDocumentDetail = components['schemas']['DocumentDetailOut']
export type Insights = components['schemas']['InsightsOut']

/** Error from the vault API carrying the problem+json `code`. */
export class VaultError extends Error {
  constructor(public code: string, public status: number, public reason?: string) {
    super(`${code} (${status})`)
  }
}

async function problemCode(res: Response): Promise<string> {
  try {
    return ((await res.json()) as { code?: string }).code ?? 'http_error'
  } catch {
    return 'http_error'
  }
}

async function vaultJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body) headers.set('Content-Type', 'application/json')
  const res = await apiFetch(path, { ...init, headers })
  if (!res.ok) throw new VaultError(await problemCode(res), res.status)
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

/**
 * GET /documents. Refetches every 2 s while any document is between uploaded and analysing;
 * react-query pauses interval refetching while the tab is hidden.
 */
export function useDocuments() {
  return useQuery({
    queryKey: ['documents'],
    queryFn: () => vaultJson<{ items: VaultDocument[] }>('/api/v1/documents'),
    refetchInterval: (q) => (hasActiveDocuments(q.state.data?.items) ? 2000 : false),
    refetchIntervalInBackground: false,
  })
}

/** GET /documents/{id} with insights; only for ready documents. */
export function useDocumentDetail(id: string, enabled: boolean) {
  return useQuery({ queryKey: ['documents', id], enabled, queryFn: () => vaultJson<VaultDocumentDetail>(`/api/v1/documents/${id}`) })
}

/** PATCH /documents/{id}: set the user's kind. */
export function useSetKind() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, kind }: { id: string; kind: DocKind }) =>
      vaultJson<VaultDocument>(`/api/v1/documents/${id}`, { method: 'PATCH', body: JSON.stringify({ kind }) }),
    onSuccess: (_d, { id, kind }) => {
      analytics.track('document_kind_set', { document_id: id, kind })
      return qc.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}

/** POST /documents/delete-all (202 `{deleting: n}`): the user typed DELETE. Refreshes the documents list. */
export function useDeleteAllDocuments() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => vaultJson<{ deleting: number }>('/api/v1/documents/delete-all', { method: 'POST', body: JSON.stringify({ confirm: 'DELETE' }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['documents'] }),
  })
}

/** DELETE /documents/{id}. */
export function useDeleteDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => vaultJson<void>(`/api/v1/documents/${id}`, { method: 'DELETE' }),
    onSuccess: (_d, id) => {
      analytics.track('document_deleted', { document_id: id })
      qc.removeQueries({ queryKey: ['documents', id] })
      return qc.invalidateQueries({ queryKey: ['documents'] })
    },
  })
}

export interface UploadResult {
  document: VaultDocument
  /** True when the API answered 200: the same file was already in the vault. */
  duplicate: boolean
}

/** Uploads one file; injectable so tests need no XHR. */
export type Uploader = (file: File, keepOriginal: boolean, onProgress: (fraction: number) => void, signal?: AbortSignal) => Promise<UploadResult>

/**
 * POST /documents (multipart) through XHR so progress events are available. Rejects with `VaultError`
 * (`network_error` on error or timeout, `aborted` when `signal` fires, `http_error` for an unreadable body).
 */
export const uploadDocument: Uploader = (file, keepOriginal, onProgress, signal) =>
  new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    if (signal?.aborted) {
      reject(new VaultError('aborted', 0))
      return
    }
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.open('POST', `${API_URL}/api/v1/documents`)
    xhr.setRequestHeader('X-Session-Id', analytics.getSessionId())
    if (analytics.page) xhr.setRequestHeader('X-Page', analytics.page)
    const token = getAuthHooks().getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onerror = () => reject(new VaultError('network_error', 0))
    xhr.ontimeout = () => reject(new VaultError('network_error', 0))
    xhr.onabort = () => reject(new VaultError('aborted', 0))
    xhr.onload = () => {
      if (xhr.status === 401 && token) getAuthHooks().onUnauthorized(token)
      if (xhr.status === 200 || xhr.status === 201) {
        try {
          resolve({ document: JSON.parse(xhr.responseText) as VaultDocument, duplicate: xhr.status === 200 })
        } catch {
          reject(new VaultError('http_error', xhr.status))
        }
        return
      }
      let code = 'http_error'
      let reason: string | undefined
      try {
        const body = JSON.parse(xhr.responseText) as { code?: string; reason?: string }
        code = body.code ?? code
        reason = typeof body.reason === 'string' ? body.reason : undefined
      } catch {
        /* not JSON */
      }
      reject(new VaultError(code, xhr.status, reason))
    }
    const body = new FormData()
    body.append('file', file)
    body.append('keep_original', String(keepOriginal))
    xhr.send(body)
  })

