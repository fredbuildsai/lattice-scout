import { vi } from 'vitest'
import { Emitter, type AuthClient, type AuthSession } from './auth/AuthClient'

/** In-memory AuthClient for tests. */
export function fakeAuthClient(session: AuthSession | null = { accessToken: 'tok', email: 'a@example.edu' }): AuthClient & { emitter: Emitter } {
  const emitter = new Emitter()
  let current = session
  return {
    mode: 'dev',
    emitter,
    init: async () => undefined,
    getSession: () => current,
    signInWithEmail: async () => 'signed_in',
    signInWithGoogle: async () => undefined,
    signOut: async () => {
      current = null
      emitter.emit(null)
    },
    subscribe: (l) => emitter.subscribe(l),
  }
}

export const ME_WITH_RESEARCHER = {
  account_id: '00000000-0000-0000-0000-000000000001',
  email: 'a@example.edu',
  role: 'user',
  launch_field: 'materials',
  created_at: '2026-10-20T00:00:00Z',
  consent_matching: false,
  consent_vault_signals: false,
  researcher: { researcher_id: 'r1', display_name: 'Ada Test', verified_level: 'self_claimed', import_status: 'done' },
}

export const PROFILE = {
  researcher_id: 'r1',
  display_name: 'Ada Test',
  institution: 'Example University',
  import: { status: 'done', generation: 1 },
  openalex_ids: [],
  topics: [{ name: 'Cellulose', weight: 0.9 }],
  works: [],
  works_summary: { included: 3, total: 4, uncertain: 1 },
  verified_level: 'self_claimed',
}

/** Stub global fetch with path-prefix routes returning JSON. */
export function mockApi(routes: Record<string, unknown | ((init?: RequestInit) => unknown)>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const key = Object.keys(routes).find((k) => url.includes(k))
    if (!key) return new Response('{}', { status: 404 })
    const v = routes[key]
    const body = typeof v === 'function' ? (v as (i?: RequestInit) => unknown)(init) : v
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}
