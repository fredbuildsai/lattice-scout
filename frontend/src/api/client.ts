import { analytics } from '../analytics/client'
import { getAuthHooks } from './auth-hooks'
import { API_URL } from './config'

/** Non-2xx response; `status` 401 means signed out. */
export class ApiError extends Error {
  constructor(public status: number, public path: string, public detail?: string) {
    super(`API ${status} ${path}`)
  }
}

/**
 * fetch wrapper for the backend: prefixes the API URL, sends `X-Session-Id` and `X-Page`
 * so server-side logs can be tied to the analytics session, injects the bearer token and
 * signs the user out on 401.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Session-Id', analytics.getSessionId())
  const page = analytics.page
  if (page) headers.set('X-Page', page)
  const { getToken, onUnauthorized } = getAuthHooks()
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${API_URL}${path}`, { ...init, headers })
  if (res.status === 401 && token) onUnauthorized(token)
  return res
}

/** JSON helper: throws `ApiError` on non-2xx. */
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await apiFetch(path, { ...init, headers })
  if (!res.ok) {
    let detail: string | undefined
    try {
      detail = ((await res.json()) as { detail?: string }).detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, path, detail)
  }
  return (await res.json()) as T
}

export type { paths, components } from './schema'
