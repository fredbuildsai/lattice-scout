import { useQueryClient } from '@tanstack/react-query'
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { setAuthHooks } from '../api/auth-hooks'
import type { AuthClient, AuthSession } from './AuthClient'
import { createAuthClient } from './createAuthClient'

interface AuthState {
  client: AuthClient
  ready: boolean
  session: AuthSession | null
}

const AuthContext = createContext<AuthState | null>(null)

/** Provides the session, wires the bearer token into the api client and clears caches on sign-out. */
export function AuthProvider({ children, client: given }: { children: ReactNode; client?: AuthClient }) {
  const client = useMemo(() => given ?? createAuthClient(), [given])
  const qc = useQueryClient()
  const [ready, setReady] = useState(false)
  const [session, setSession] = useState<AuthSession | null>(null)

  useEffect(() => {
    setAuthHooks({
      getToken: () => client.getSession()?.accessToken ?? null,
      onUnauthorized: () => void client.signOut(),
    })
    const unsub = client.subscribe((s) => {
      setSession(s)
      if (!s) qc.clear()
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
  }, [client, qc])

  const value = useMemo(() => ({ client, ready, session }), [client, ready, session])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Current auth state and client. */
export function useAuth(): AuthState {
  const v = useContext(AuthContext)
  if (!v) throw new Error('useAuth outside AuthProvider')
  return v
}
