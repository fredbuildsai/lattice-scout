import { analytics } from '../analytics/client'
import { API_URL } from './config'

/**
 * fetch wrapper for the backend: prefixes the API URL and sends `X-Session-Id` and `X-Page`
 * so server-side logs and usage records can be tied to the analytics session.
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Session-Id', analytics.getSessionId())
  const page = analytics.page
  if (page) headers.set('X-Page', page)
  return fetch(`${API_URL}${path}`, { ...init, headers })
}

/** JSON helper: throws on non-2xx. */
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await apiFetch(path, init)
  if (!res.ok) throw new Error(`API ${res.status} ${path}`)
  return (await res.json()) as T
}

export type { paths, components } from './schema'
