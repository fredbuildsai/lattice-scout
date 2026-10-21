import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { setAuthHooks } from '../api/auth-hooks'
import type { AuthClient, AuthSession } from './AuthClient'
import { createAuthClient } from './createAuthClient'

interface AuthState {
  client: AuthClient
  ready: boolean
  session: AuthSession | null
  /** Sign out and show the "account is being deleted" page. */
  accountDeleted: () => void
}

const AuthContext = createContext<AuthState | null>(null)

/** Provides the session, wires the bearer token into the api client and clears caches on sign-out. */
export function AuthProvider({ children, client: given }: { children: ReactNode; client?: AuthClient }) {
  const client = useMemo(() => given ?? createAuthClient(), [given])
  const qc = useQueryClient()
  const [ready, setReady] = useState(false)
  const [session, setSession] = useState<AuthSession | null>(null)
  const [deleted, setDeleted] = useState(false)
  const accountDeleted = useCallback(() => {
    setDeleted(true)
    void client.signOut()
  }, [client])

  useEffect(() => {
    setAuthHooks({
      getToken: () => client.getSession()?.accessToken ?? null,
      onUnauthorized: (tok) => void (client.handleUnauthorized ? client.handleUnauthorized(tok) : client.signOut()),
      onAccountDeleted: accountDeleted,
    })
    let lastToken = client.getSession()?.accessToken ?? null
    const unsub = client.subscribe((s) => {
      setSession(s)
      if (!s) qc.clear()
      else if (s.accessToken !== lastToken) void qc.invalidateQueries() // renewed token: retry what failed
      lastToken = s?.accessToken ?? null
    })
    let live = true
    void client.init().then(() => {
      if (!live) return
      setSession(client.getSession())
      setReady(true)
    })
    return () => {
      live = false
      unsub()
    }
  }, [client, qc, accountDeleted])

  const value = useMemo(() => ({ client, ready, session, accountDeleted }), [client, ready, session, accountDeleted])
  return <AuthContext.Provider value={value}>
      {deleted && !session ? (
        <main role="status" className="mx-auto max-w-[480px] p-8 text-ink">
          <h1 className="font-serif text-3xl font-semibold">Your account is being deleted</h1>
          <p className="mt-3 text-ink-2">You have been signed out. Your files, profile and matches are being removed. You can close this page.</p>
          <button type="button" onClick={() => setDeleted(false)} className="mt-5 min-h-11 rounded-btn border border-line-strong px-4 text-sm font-semibold">
            Back to sign in
          </button>
        </main>
      ) : (
        children
      )}
    </AuthContext.Provider>
}

/** Current auth state and client. */
export function useAuth(): AuthState {
  const v = useContext(AuthContext)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
